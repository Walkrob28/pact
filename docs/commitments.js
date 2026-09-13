/* Client-side port of commitments.py — same rules, so the public demo behaves
   identically to the server. Kept in lockstep with the Python version. */
const PLEDGE = /\b(i|we)\s*(?:'|’)?(?:ll|ve got to)\b|\b(?:i|we)\s+(?:will|shall|can|could|am going to|are going to|'m going to|'re going to)\b|\b(?:i'm|we're|i am|we are)\s+(?:going to|gonna|about to)\b|\blet me\b|\bi'll go ahead and\b|\bremind me to\b/i;
const HEDGE = /\b(maybe|might|possibly|probably|if i (?:get|have) (?:time|a chance)|no promises|tentatively)\b/i;
const V = "send|share|email|forward|set up|schedule|book|draft|write|review|check|confirm|update|prepare|put together|pull together|introduce|intro|sign|deliver|ship|fix|call|ping|invoice|quote|price|submit|file|refund|escalate|walk through|get back|circle back|follow up|loop in|loop back|hand off|turn around|kick off|send over|send across";
const ACTION = new RegExp(
  `\\b(?:${V})(?:s|ed|ing)?\\b` +
  `|\\b(?:sent|sending|drafted|drafting|written|writing|wrote|got|get)\\b\\s+(?:\\w+\\s+){0,3}(?:${V})(?:s|ed|ing|en)?\\b` +
  `|\\bget\\s+(?:\\w+\\s+){0,3}(?:sent|drafted|written|scheduled|booked|signed|reviewed|confirmed|fixed|shipped|over to you|back to you)\\b` +
  `|\\b(?:get|have|bring|give)\\s+(?:you|them|him|her|us|y'all)\\b` +
  `|\\b(?:over|back|across)\\s+to\\s+(?:you|them|us)\\b` +
  `|\\b(?:have|get|send|bring|push|route)\\s+(?:\\w+\\s+){0,4}?to\\s+(?:you|them|us)\\b`, "i");
const WD = "monday|tuesday|wednesday|thursday|friday|saturday|sunday";
const DEADLINE = new RegExp(
  `\\b(?:by|before|no later than|on|this|next)\\s+(?:${WD}|eod|end of (?:day|week|month)|tomorrow|today|tonight|morning|afternoon|week|month|quarter|the end of the (?:day|week|month))\\b` +
  `|\\b(?:tomorrow|today|tonight|asap|right away|first thing)\\b` +
  `|\\bin\\s+(?:a\\s+)?(?:\\d+|a|two|three|four|five|couple of|few)\\s+(?:minutes?|hours?|days?|weeks?)\\b` +
  `|\\bwithin\\s+(?:the\\s+)?(?:\\d+|a|two|three|24|48|72)\\s*(?:-|\\s)?(?:minutes?|hours?|days?|weeks?|business days?)\\b` +
  `|\\bby\\s+(?:the\\s+)?\\d{1,2}(?:st|nd|rd|th)?\\b|\\bnext\\s+(?:week|month|${WD})\\b`, "i");
const ASK = /\b(can|could|would|will)\s+you\b|\bplease\s+(?:send|share|confirm|review)\b/i;

function extract(text, owner = "Unknown", turnIndex = -1) {
  text = String(text || "").replace(/\s+/g, " ").trim();
  if (!text) return [];
  const out = [];
  for (let clause of text.split(/(?<=[.!?])\s+|\s+(?:and then|and also|then i'll|,\s*and)\s+/)) {
    clause = clause.replace(/\s+/g, " ").trim();
    if (clause.length < 8) continue;
    if (ASK.test(clause) && !PLEDGE.test(clause)) continue;
    if (!PLEDGE.test(clause)) continue;
    if (!ACTION.test(clause)) continue;
    const dl = clause.match(DEADLINE);
    let conf = 0.55 + (dl ? 0.25 : 0) + 0.10 - (HEDGE.test(clause) ? 0.35 : 0);
    conf = Math.max(0, Math.min(1, conf));
    if (conf < 0.4) continue;
    const tags = [];
    if (HEDGE.test(clause)) tags.push("hedged");
    if (!dl) tags.push("no-deadline");
    out.push({text: clause, owner, deadline: dl ? dl[0] : null,
              confidence: Math.round(conf * 100) / 100, turn_index: turnIndex, tags});
  }
  return out;
}
