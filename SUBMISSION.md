# Submission copy — AssemblyAI Voice Agent Hackathon (deadline 2026-09-30)

## Project name
Pact

## Tagline (one line)
Catch every promise made on a call — who owes what, by when.

## Short description (~50 words)
Sales calls end in promises that nobody writes down. Pact listens live and captures every
commitment as it's spoken — the revised contract, the report, the intro — then attributes each
one to a real speaker when the call ends. Not a transcript. Not a summary. The obligations.

## Full description
Every sales call ends with promises. "I'll send the revised MSA by Friday." "I'll get you a
decision within 48 hours." By the next morning, half of them exist only in someone's memory,
and that's where deals stall.

Pact is a voice agent that captures commitments in real time and pins each one to the person
who made it.

**The problem it had to solve.** AssemblyAI splits two capabilities we both needed: streaming
gives sub-second turns but no speaker identity, while speaker diarization is async-only. Rather
than pick one, Pact runs two passes — and that constraint became the product.

The **live pass** uses Universal-Streaming (v3) over WebSocket, surfacing commitments mid-call
with sub-second latency. Because streaming genuinely cannot tell who is speaking, Pact marks
them *attribution pending* and never guesses. (An early build labelled speakers by alternating
turns; testing against real audio proved it was flat wrong, so we removed it.)

The **close-out pass** re-runs the recorded audio through async transcription with
`speaker_labels: true` and resolves every commitment to a real speaker, with timestamps.

Live speed during the call, accurate attribution the moment it ends.

**Distinguishing a promise from a request** is the hard part, and it's where most of the
engineering went. A commitment requires a first-person pledge *and* an actionable verb.
"I'll send the quote Friday" counts. "Can you send me the quote?" is an ask — dropped.
"I'll be at the conference" has no deliverable — dropped. "I'll maybe get to it" is scored
lower and flagged as hedged. Deadlines are extracted and attached; multi-promise sentences are
split; streaming re-emissions are de-duplicated.

**Measured**, not asserted: on a 46-second two-speaker call run through the live API, Pact
caught 6 of 6 commitments, attributed 100% of them correctly, produced the full close-out in
7.5 seconds, and raised zero false positives on requests. The extraction engine ships with 15
regression tests covering the hard negatives.

## How AssemblyAI is used
- **Universal-Streaming (v3)** over WebSocket — real-time turn transcription for the live pass.
- **Async transcription with `speaker_labels: true`** — speaker diarization for the close-out
  pass, including per-utterance timestamps used to jump to the moment a promise was made.

The two-pass architecture exists specifically because of where AssemblyAI draws the line
between these capabilities.

## Tech stack
Python · FastAPI · WebSockets · AssemblyAI Universal-Streaming + Speaker Diarization ·
vanilla JS front end (no build step) · AudioWorklet for PCM16 mic capture

## Links
- Live demo: https://walkrob28.github.io/pact/
- Repository: https://github.com/Walkrob28/pact
- Demo video: <paste video URL>

## Who it's for
Sales teams, customer success, agencies, and field-service businesses — anywhere a promise made
out loud turns into work someone owes.
