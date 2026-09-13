from commitments import extract, dedupe

# (utterance, should_find_commitment, note)
CASES = [
    # --- true commitments ---
    ("I'll send over the revised quote by Friday.", True, "classic pledge + deadline"),
    ("We'll get the contract drafted and over to you by end of week.", True, "we-form"),
    ("Let me pull together the pricing sheet and email it tomorrow.", True, "let me + action"),
    ("I'm going to loop in our solutions engineer this afternoon.", True, "going to"),
    ("I'll follow up with the security questionnaire within 48 hours.", True, "within N hours"),
    ("We can schedule the onboarding call for next Tuesday.", True, "we can + schedule"),
    ("I'll go ahead and refund that invoice today.", True, "go ahead and"),
    ("I'll review the SOW and confirm by the 15th.", True, "by the Nth"),

    # --- NOT commitments (the hard part) ---
    ("Can you send me the pricing by Friday?", False, "ask, not a promise"),
    ("Please share the contract when you get a chance.", False, "request"),
    ("I'll be at the conference next week.", False, "no deliverable"),
    ("We will definitely be excited about that.", False, "no action verb"),
    ("So the platform handles transcription in real time.", False, "statement of fact"),
    ("Yeah, that makes sense to me.", False, "filler"),
    ("Our team usually sends those out on Mondays.", False, "habitual, not a pledge"),
]

def main():
    passed = failed = 0
    for text, expect, note in CASES:
        got = extract(text, owner="Rep")
        ok = bool(got) == expect
        if ok: passed += 1
        else:
            failed += 1
            print(f"  FAIL [{note}]\n        {text!r}\n        expected={expect} got={[c.text for c in got]}")
    print(f"\ndetection: {passed}/{len(CASES)} passed, {failed} failed")

    print("\n--- deadline + confidence extraction ---")
    for t in ["I'll send over the revised quote by Friday.",
              "I'll maybe get to the report if I have time.",
              "We'll ship the fix asap.",
              "I'll draft the proposal."]:
        for c in extract(t, owner="Rep"):
            print(f"  {c.confidence:.2f} | deadline={c.deadline!r:22} tags={c.tags} | {c.text}")

    print("\n--- multi-commitment clause splitting ---")
    multi = "I'll send the quote by Monday and then I'll schedule the demo for next week."
    for c in extract(multi, owner="Rep"):
        print(f"  -> {c.text}  (due: {c.deadline})")

    print("\n--- dedupe across streaming re-emissions ---")
    dupes = extract("I'll send the quote by Friday.", turn_index=1) + \
            extract("I'll send the quote by Friday.", turn_index=1)
    print(f"  {len(dupes)} raw -> {len(dedupe(dupes))} after dedupe")
    return failed

if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
