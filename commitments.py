"""
Commitment extraction.

A 'commitment' is a speaker promising a future action. We care about three things:
  who owes it, what it is, and when it's due.

This module is deliberately dependency-free and deterministic so it can run on every
streaming turn in real time (no LLM round-trip in the hot path) and be unit-tested
without network access. The async close-out pass refines these with speaker labels.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, asdict, field

# --- first-person future intent: the speaker binding themselves to an action -------
PLEDGE = re.compile(
    r"\b(i|we)\s*(?:'|’)?(?:ll|ve got to)\b"
    r"|\b(?:i|we)\s+(?:will|shall|can|could|am going to|are going to|'m going to|'re going to)\b"
    r"|\b(?:i'm|we're|i am|we are)\s+(?:going to|gonna|about to)\b"
    r"|\blet me\b|\bi'll go ahead and\b|\bremind me to\b",
    re.I,
)
# --- hedges that cancel a pledge ("I'll try to maybe...") --------------------------
HEDGE = re.compile(r"\b(maybe|might|possibly|probably|if i (?:get|have) (?:time|a chance)|no promises|tentatively)\b", re.I)
# --- action verbs that make a pledge actionable rather than conversational ---------
# verbs are matched with their inflections: draft/drafts/drafted/drafting, send/sends/sent/sending...
_VERBS = ("send|share|email|forward|set up|schedule|book|draft|write|review|check|confirm|update|"
          "prepare|put together|pull together|introduce|intro|sign|deliver|ship|fix|call|ping|"
          "invoice|quote|price|submit|file|refund|escalate|walk through|get back|circle back|"
          "follow up|loop in|loop back|hand off|turn around|kick off|send over|send across")
ACTION = re.compile(rf"\b(?:{_VERBS})(?:s|ed|ing)?\b"
                    r"|\b(?:sent|sending|drafted|drafting|written|writing|wrote|got|get)\b\s+"
                    rf"(?:\w+\s+){{0,3}}(?:{_VERBS})(?:s|ed|ing|en)?\b"
                    r"|\bget\s+(?:\w+\s+){0,3}(?:sent|drafted|written|scheduled|booked|signed|"
                    r"reviewed|confirmed|fixed|shipped|over to you|back to you)\b"
                    # "I'll get you an answer", "I'll have the numbers to you", "I'll bring you a quote"
                    r"|\b(?:get|have|bring|give)\s+(?:you|them|him|her|us|y'all)\b"
                    r"|\b(?:over|back|across)\s+to\s+(?:you|them|us)\b"
                    r"|\b(?:have|get|send|bring|push|route)\s+(?:\w+\s+){0,4}?to\s+(?:you|them|us)\b",
    re.I,
)
# --- deadline language -------------------------------------------------------------
WEEKDAY = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
DEADLINE = re.compile(
    rf"\b(?:by|before|no later than|on|this|next)\s+(?:{WEEKDAY}|eod|end of (?:day|week|month)|"
    rf"tomorrow|today|tonight|morning|afternoon|week|month|quarter|the end of the (?:day|week|month))\b"
    rf"|\b(?:tomorrow|today|tonight|asap|right away|first thing)\b"
    rf"|\bin\s+(?:a\s+)?(?:\d+|a|two|three|four|five|couple of|few)\s+(?:minutes?|hours?|days?|weeks?)\b"
    rf"|\bwithin\s+(?:the\s+)?(?:\d+|a|two|three|24|48|72)\s*(?:-|\s)?(?:minutes?|hours?|days?|weeks?|business days?)\b"
    rf"|\bby\s+(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)?\b"
    rf"|\bnext\s+(?:week|month|{WEEKDAY})\b",
    re.I,
)
# --- questions/requests aimed at the OTHER party: an ask, not a commitment ---------
ASK = re.compile(r"\b(can|could|would|will)\s+you\b|\bplease\s+(?:send|share|confirm|review)\b", re.I)


@dataclass
class Commitment:
    text: str
    owner: str = "Unknown"          # replaced with a real speaker label in the close-out pass
    deadline: str | None = None
    confidence: float = 0.0
    turn_index: int = -1
    start_ms: int | None = None     # lets the UI jump to the moment it was said
    tags: list[str] = field(default_factory=list)

    def to_dict(self): return asdict(self)


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()


def extract(text: str, *, owner: str = "Unknown", turn_index: int = -1,
            start_ms: int | None = None) -> list[Commitment]:
    """Pull commitments out of one utterance. Returns [] for ordinary conversation."""
    text = _clean(text)
    if not text:
        return []
    out: list[Commitment] = []
    # split into clauses so "I'll send the quote and you review the contract" yields one commitment
    for clause in re.split(r"(?<=[.!?])\s+|\s+(?:and then|and also|then i'll|,\s*and)\s+", text):
        clause = _clean(clause)
        if len(clause) < 8:
            continue
        if ASK.search(clause) and not PLEDGE.search(clause):
            continue                       # "can you send it?" is a request, not a promise
        if not PLEDGE.search(clause):
            continue
        if not ACTION.search(clause):
            continue                       # "I'll be there" — no deliverable
        dl = DEADLINE.search(clause)
        conf = 0.55
        if dl: conf += 0.25
        if ACTION.search(clause): conf += 0.10
        if HEDGE.search(clause): conf -= 0.35
        tags = []
        if HEDGE.search(clause): tags.append("hedged")
        if not dl: tags.append("no-deadline")
        conf = max(0.0, min(1.0, conf))
        if conf < 0.4:
            continue
        out.append(Commitment(text=clause, owner=owner, deadline=dl.group(0) if dl else None,
                              confidence=round(conf, 2), turn_index=turn_index,
                              start_ms=start_ms, tags=tags))
    return out


def dedupe(items: list[Commitment]) -> list[Commitment]:
    """Streaming re-emits turns as they finalize; keep the highest-confidence version of each."""
    seen: dict[str, Commitment] = {}
    for c in items:
        key = re.sub(r"[^a-z0-9 ]", "", c.text.lower())[:60]
        if key not in seen or c.confidence > seen[key].confidence:
            seen[key] = c
    return sorted(seen.values(), key=lambda c: (c.turn_index, -c.confidence))
