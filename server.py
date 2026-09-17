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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
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




SALES_NOTES_PROMPT = """You are an expert sales assistant taking notes on a sales call for the rep.

Transcript (speaker-labelled):
{{ transcript }}

From the transcript above, produce concise, skimmable notes. Include only sections that apply:

**Commitments** - who committed to what, by when (be specific about dates/deadlines)
**Key numbers** - pricing, quantities, budget, or contract values mentioned
**Timeline / purchasing cycle** - decision timeframe and next milestones
**Decision-makers & roles** - who is involved and their role in the decision
**Pain points / needs** - problems the prospect raised
**Objections** - concerns raised and how they were handled
**Next steps** - concrete follow-up actions

Use the speaker labels to attribute. Be specific and terse. Do NOT invent anything \
that is not clearly in the transcript."""


LLM_GATEWAY = "https://llm-gateway.assemblyai.com/v1/chat/completions"
LLM_MODEL = os.getenv("PACT_LLM_MODEL", "qwen3.5-4b-32k-fast")

async def _gateway_notes(tid: str, prompt: str = SALES_NOTES_PROMPT) -> str | None:
    """Run an LLM over the transcript via AssemblyAI's LLM Gateway (uses the AAI key we
    already have; transcript_id injects the transcript into the {{ transcript }} tag).
    Returns markdown notes, or None on failure so we fall back to the regex commitments."""
    try:
        async with httpx.AsyncClient(timeout=90) as cx:
            r = await cx.post(LLM_GATEWAY,
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": LLM_MODEL, "transcript_id": tid, "max_tokens": 700,
                      "temperature": 0.2,
                      "messages": [{"role": "user", "content": prompt}]})
            if r.status_code < 300:
                txt = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
                return txt.strip() or None
    except Exception:
        pass
    return None


async def _delete_transcript(tid: str) -> None:
    """Privacy: once we have the notes, delete the transcript + stored audio from
    AssemblyAI so no recording is retained after the call."""
    try:
        async with httpx.AsyncClient(timeout=30) as cx:
            await cx.delete(f"{AAI_HTTP}/transcript/{tid}", headers={"authorization": API_KEY})
    except Exception:
        pass  # best-effort; never fail the call over cleanup


def _format_notes_email(items: list[dict], smart_notes: str | None = None) -> tuple[str, str]:
    """Build the follow-up email (subject, HTML) from the extracted commitments."""
    n = len(items)
    subject = f"Your call follow-ups — {n} commitment{'s' if n != 1 else ''}"
    if not items:
        rows = "<p style='color:#667'>No clear commitments were made on this call.</p>"
    else:
        rows = ""
        for c in items:
            due = f" &middot; <b>due {c['deadline']}</b>" if c.get("deadline") else ""
            rows += (f"<li style='margin:0 0 12px;line-height:1.5'>"
                     f"<span style='color:#5b8cff;font-weight:600'>{c.get('owner','')}</span>{due}<br>"
                     f"{c['text']}</li>")
        rows = f"<ul style='padding-left:18px;margin:0'>{rows}</ul>"
    notes_html = ""
    if smart_notes:
        body = smart_notes.replace("**", "").replace("\n", "<br>")
        notes_html = (f"<div style='background:#f4f7ff;border-radius:10px;padding:14px 16px;margin:0 0 18px;"
                      f"font-size:14px;line-height:1.6'>{body}</div>")
    html = (f"<div style='font-family:system-ui,Arial,sans-serif;max-width:560px'>"
            f"<h2 style='margin:0 0 4px'>Call follow-ups</h2>"
            f"<p style='color:#667;margin:0 0 18px'>Captured by Pact &middot; the recording has been deleted.</p>"
            f"{notes_html}{rows}"
            f"<p style='color:#99a;font-size:12px;margin-top:24px'>Pact listens for commitments and emails them to you, then deletes the recording for privacy.</p></div>")
    return subject, html


async def _send_email(to: str, subject: str, html: str) -> bool:
    """Send via Resend if configured. Returns True if actually sent."""
    key = os.getenv("RESEND_API_KEY", "").strip()
    if not (key and to):
        return False
    sender = os.getenv("PACT_FROM_EMAIL", "Pact <onboarding@resend.dev>")
    try:
        async with httpx.AsyncClient(timeout=30) as cx:
            r = await cx.post("https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}"},
                json={"from": sender, "to": [to], "subject": subject, "html": html})
            return r.status_code < 300
    except Exception:
        return False


@app.post("/api/finalize")
async def finalize(audio: UploadFile = File(...), email: str = Form(None)):
    """The accurate pass: real speaker attribution for every commitment, then the
    recording is deleted and (optionally) the notes are emailed."""
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

    # smart notes: run Claude over the transcript (via LeMUR) BEFORE we delete it
    smart_notes = None
    if d.get("id"):
        smart_notes = await _gateway_notes(d["id"])

    # privacy: delete the transcript + stored audio now that we have the notes
    if d.get("id"):
        await _delete_transcript(d["id"])

    # email the notes (or, with no provider configured, return them to show in-app)
    subject, email_html = _format_notes_email(items, smart_notes)
    emailed = await _send_email(email, subject, email_html) if email else False
    return {"commitments": items, "count": len(items),
            "recording_deleted": bool(d.get("id")), "emailed": emailed, "smart_notes": smart_notes,
            "email_subject": subject, "email_html": email_html,
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
