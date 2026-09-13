"""
Pact — catch every promise made on a call.

Two passes, because AssemblyAI splits these capabilities:
  LIVE     : streaming WebSocket (v3) gives low-latency turns -> commitments appear as spoken.
             Streaming does NOT provide speaker identity, only turn boundaries.
  CLOSE-OUT: async transcription with speaker_labels=true re-runs the audio and attributes
             every commitment to a real speaker, producing the accurate follow-up list.
"""
from __future__ import annotations
import asyncio, json, os, time, urllib.parse
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from commitments import extract, dedupe, Commitment

def _load_env(path: Path) -> None:
    """Minimal .env loader — avoids a dependency and never overrides a real env var."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v.strip():
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env(Path(__file__).parent / ".env")
API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "").strip()
AAI_WS = "wss://streaming.assemblyai.com/v3/ws"
AAI_HTTP = "https://api.assemblyai.com/v2"
SAMPLE_RATE = 16_000

BASE = Path(__file__).parent
app = FastAPI(title="Pact")


@app.get("/api/health")
async def health():
    return {"ok": True, "key_configured": bool(API_KEY),
            "mode": "live" if API_KEY else "demo-only"}


# ---------------------------------------------------------------- live streaming
@app.websocket("/ws/stream")
async def ws_stream(client: WebSocket):
    """Browser mic -> us -> AssemblyAI streaming -> turns -> commitments -> browser."""
    await client.accept()
    if not API_KEY:
        await client.send_json({"type": "error",
            "message": "ASSEMBLYAI_API_KEY not set on the server. Use Demo mode."})
        await client.close(); return

    qs = urllib.parse.urlencode({"sample_rate": SAMPLE_RATE, "format_turns": "true"})
    found: list[Commitment] = []
    turn_i = 0
    try:
        async with websockets.connect(f"{AAI_WS}?{qs}",
                                      additional_headers={"Authorization": API_KEY},
                                      max_size=None) as aai:

            async def pump_audio():
                """Forward raw PCM16 frames from the browser to AssemblyAI."""
                try:
                    while True:
                        msg = await client.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if (b := msg.get("bytes")) is not None:
                            await aai.send(b)
                        elif (t := msg.get("text")) and json.loads(t).get("action") == "stop":
                            await aai.send(json.dumps({"type": "Terminate"}))
                            break
                except (WebSocketDisconnect, RuntimeError):
                    pass

            async def pump_events():
                nonlocal turn_i
                async for raw in aai:
                    ev = json.loads(raw)
                    kind = ev.get("type")
                    if kind == "Turn":
                        text = ev.get("transcript", "") or ""
                        final = bool(ev.get("end_of_turn"))
                        await client.send_json({"type": "transcript", "text": text,
                                                "final": final, "turn": turn_i})
                        if final and text.strip():
                            # streaming gives turns, not identity — never fabricate a speaker.
                            # the close-out pass fills this in from speaker_labels.
                            for c in extract(text, owner="Attribution pending",
                                             turn_index=turn_i):
                                found.append(c)
                                await client.send_json({"type": "commitment", **c.to_dict()})
                            turn_i += 1
                    elif kind in ("Termination", "SessionTerminated"):
                        break
                    elif kind == "Error":
                        await client.send_json({"type": "error", "message": ev.get("error", "stream error")})

            await asyncio.gather(pump_audio(), pump_events())
    except Exception as e:
        try: await client.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})
        except Exception: pass
    finally:
        try:
            await client.send_json({"type": "done",
                "commitments": [c.to_dict() for c in dedupe(found)]})
            await client.close()
        except Exception:
            pass


# ---------------------------------------------------------- close-out (diarized)
async def _transcribe_with_speakers(audio: bytes) -> dict:
    """Upload -> request diarized transcript -> poll to completion."""
    h = {"authorization": API_KEY}
    async with httpx.AsyncClient(timeout=120) as cx:
        up = await cx.post(f"{AAI_HTTP}/upload", headers=h, content=audio)
        up.raise_for_status()
        url = up.json()["upload_url"]

        job = await cx.post(f"{AAI_HTTP}/transcript", headers=h, json={
            "audio_url": url, "speaker_labels": True, "speakers_expected": 2,
            "punctuate": True, "format_text": True})
        job.raise_for_status()
        tid = job.json()["id"]

        deadline = time.time() + 300
        while time.time() < deadline:
            r = await cx.get(f"{AAI_HTTP}/transcript/{tid}", headers=h)
            r.raise_for_status()
            d = r.json()
            if d["status"] == "completed":
                return d
            if d["status"] == "error":
                raise RuntimeError(d.get("error", "transcription failed"))
            await asyncio.sleep(2)
    raise TimeoutError("transcription timed out")


@app.post("/api/finalize")
async def finalize(audio: UploadFile = File(...)):
    """The accurate pass: real speaker attribution for every commitment."""
    if not API_KEY:
        return JSONResponse({"error": "ASSEMBLYAI_API_KEY not set"}, status_code=400)
    try:
        data = await audio.read()
        d = await _transcribe_with_speakers(data)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)

    found: list[Commitment] = []
    for i, u in enumerate(d.get("utterances") or []):
        found += extract(u.get("text", ""), owner=f"Speaker {u.get('speaker','?')}",
                         turn_index=i, start_ms=u.get("start"))
    items = [c.to_dict() for c in dedupe(found)]
    return {"commitments": items, "count": len(items),
            "duration_ms": d.get("audio_duration", 0) * 1000,
            "transcript": d.get("text", "")[:5000]}


# ------------------------------------------------------- demo (no API key needed)
@app.post("/api/demo-extract")
async def demo_extract(payload: dict):
    """Runs the scripted demo call through the real extraction path, so demo mode
    exercises the same code as a live call (and works with no API key)."""
    found: list[Commitment] = []
    for i, row in enumerate(payload.get("turns") or []):
        who, text = (row + ["", ""])[:2] if isinstance(row, list) else ("", "")
        found += extract(text, owner=who or f"Speaker {(i % 2) + 1}", turn_index=i)
    items = [c.to_dict() for c in dedupe(found)]
    return {"commitments": items, "count": len(items)}


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")


if __name__ == "__main__":
    # PORT is supplied by the harness when autoPort is enabled; 8000 is a local default.
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
