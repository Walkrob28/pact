# Pact — catch every promise made on a call

Pact listens to a sales or support call and captures every **commitment** someone makes —
who owes what, by when — as it's spoken, then attributes each one to a real speaker when
the call ends.

**Live demo:** https://walkrob28.github.io/pact/ (scripted demo call, runs entirely in-browser)

Not a meeting summary and not a transcript. Specifically the obligations.

## Why two passes

AssemblyAI splits these capabilities, and that constraint shaped the design:

| | Live pass | Close-out pass |
|---|---|---|
| Transport | Streaming WebSocket (`v3`) | Async `/v2/transcript` |
| Latency | Sub-second, mid-call | Seconds, after the call |
| Speaker identity | **Not available** — streaming emits turns, not speakers | `speaker_labels: true` |
| What Pact shows | Commitments marked *attribution pending* | Each commitment against a real speaker |

Streaming cannot diarize, so Pact **never guesses who spoke** during the live pass — an earlier
`turn % 2` placeholder was measurably wrong. The close-out pass resolves identity properly.

## Measured

A 46-second two-speaker call through the live API:

- **6/6** commitments caught
- **100%** correctly attributed to the right speaker
- **7.5s** for the full close-out pass
- **0** false positives on requests ("Can you send me the quote?" is an ask, not a promise)

## What counts as a commitment

Requires a first-person pledge **and** an actionable verb. Deadlines are extracted, confidence
is scored, and hedged promises are flagged.

```
"I'll send over the revised MSA by Friday."        -> commitment, due Friday, 0.90
"I'll get you a decision within 48 hours."          -> commitment, due within 48 hours, 0.90
"I'll draft the proposal."                          -> commitment, no deadline, 0.65
"I'll maybe get to it if I have time."              -> dropped (hedged)
"Can you send me the pricing by Friday?"            -> dropped (a request, not a promise)
"I'll be at the conference next week."              -> dropped (no deliverable)
```

`test_commitments.py` covers these, including the hard negatives. All 15 pass.

## Run locally

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
echo 'ASSEMBLYAI_API_KEY=your_key' > .env
./.venv/bin/python server.py            # http://127.0.0.1:8000
```

- `▶ Demo call` — scripted call, no key needed
- `🎬 Present` — the same demo with caption overlays
- `● Start live call` — real microphone, streaming + diarized close-out (needs the key)

## Layout

```
commitments.py       extraction engine (dependency-free, unit-tested)
test_commitments.py  15 regression tests
server.py            FastAPI: /ws/stream, /api/finalize, /api/demo-extract, /api/health
static/              app UI
docs/                static build for GitHub Pages (extractor ported to JS, same rules)
deck/                pitch deck
```

Built with [AssemblyAI](https://www.assemblyai.com/) Universal-Streaming and speaker diarization.
