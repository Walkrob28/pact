const $ = s => document.querySelector(s);
const elT = $("#transcript"), elC = $("#commitments"), elStatus = $("#status");
let ws, audioCtx, node, stream, recorder, chunks = [], turns = 0, commits = 0, running = false;

const setStatus = (t, cls="") => { elStatus.textContent = t; elStatus.className = "pill " + cls; };
const clear = el => { el.innerHTML = ""; };

function addTurn(text, final, who="") {
  const first = elT.querySelector(".empty"); if (first) first.remove();
  let p = elT.querySelector(".turn.partial");
  if (!final) {
    if (!p) { p = document.createElement("div"); p.className = "turn partial"; elT.appendChild(p); }
    p.textContent = text;
  } else {
    if (p) p.remove();
    const d = document.createElement("div");
    d.className = "turn";
    d.innerHTML = (who ? `<span class="who">${who}</span>` : "") + escapeHtml(text);
    elT.appendChild(d);
    $("#turnCount").textContent = `${++turns} turns`;
  }
  elT.scrollTop = elT.scrollHeight;
}

function addCommitment(c, final=false) {
  const first = elC.querySelector(".empty"); if (first) first.remove();
  const d = document.createElement("div");
  d.className = "card" + (final ? " final" : "");
  d.innerHTML = `
    <div class="who${(c.owner||"").startsWith("Attribution") ? " pending" : ""}">${escapeHtml(c.owner || "Unknown")}</div>
    <div class="txt">${escapeHtml(c.text)}</div>
    <div class="meta">
      ${c.deadline ? `<span class="chip due">⏱ ${escapeHtml(c.deadline)}</span>` : `<span class="chip">no deadline</span>`}
      <span class="chip conf">${Math.round((c.confidence||0)*100)}% confident</span>
      ${(c.tags||[]).filter(t=>t!=="no-deadline").map(t=>`<span class="chip">${escapeHtml(t)}</span>`).join("")}
    </div>`;
  elC.appendChild(d);
  $("#cCount").textContent = ++commits;
  elC.scrollTop = elC.scrollHeight;
}

const escapeHtml = s => String(s).replace(/[&<>"']/g, m =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));

function reset() {
  clear(elT); clear(elC); turns = commits = 0;
  $("#turnCount").textContent = "0 turns"; $("#cCount").textContent = "0";
  $("#passLabel").textContent = "live pass"; $("#finalizeBar").hidden = true;
}

function uiRunning(on) {
  running = on;
  $("#liveBtn").hidden = on; $("#demoBtn").disabled = on; $("#stopBtn").hidden = !on;
}

/* ---------------------------------------------------------------- demo mode
   A scripted call so the product is demonstrable without a mic or API key —
   it runs through the exact same rendering path as a live call. */
const DEMO = [
  ["Speaker 1", "Hey Marcus, thanks for making time. I know the security review has been dragging."],
  ["Speaker 2", "No problem. We're close, but legal flagged two clauses in the MSA."],
  ["Speaker 1", "Understood. I'll send over the revised MSA with those clauses struck by Friday."],
  ["Speaker 2", "That would help. Can you also share the SOC 2 report?"],
  ["Speaker 1", "Absolutely. Let me pull together the SOC 2 and the pen test summary and email them tomorrow."],
  ["Speaker 2", "Perfect. On our side I'll review the pricing tiers with finance this afternoon."],
  ["Speaker 1", "Great. And I'm going to loop in our solutions engineer for the integration questions."],
  ["Speaker 2", "We'll need that before we can commit to the timeline."],
  ["Speaker 2", "I'll get you a decision on the enterprise tier within 48 hours."],
  ["Speaker 1", "Sounds good. I'll schedule the technical deep dive for next Tuesday."],
  ["Speaker 2", "Works for me. Talk soon."],
];

async function runDemo() {
  reset(); uiRunning(true); setStatus("demo", "live");
  const found = [];
  DEMO.forEach(([who, text], i) => found.push(...extract(text, who, i)));
  const byTurn = {};
  found.forEach(c => (byTurn[c.turn_index] ||= []).push(c));

  for (let i = 0; i < DEMO.length && running; i++) {
    const [who, text] = DEMO[i];
    // type it out partially for realism
    for (let k = 1; k <= 3 && running; k++) {
      addTurn(text.slice(0, Math.floor(text.length * k / 4)), false);
      await sleep(90);
    }
    addTurn(text, true, who);
    (byTurn[i] || []).forEach(c => addCommitment({...c, owner: who}));
    await sleep(520);
  }
  if (running) { setStatus("demo complete", ""); $("#passLabel").textContent = "demo (scripted)"; }
  uiRunning(false);
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ---------------------------------------------------------------- live mode */
async function startLive() {
  reset();
  try { stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount:1, echoCancellation:true}}); }
  catch { setStatus("mic denied", "err"); return; }

  uiRunning(true); setStatus("connecting", "");
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/stream`);
  ws.binaryType = "arraybuffer";

  ws.onopen = async () => {
    setStatus("live", "live");
    audioCtx = new AudioContext({sampleRate: 16000});
    const blobURL = URL.createObjectURL(new Blob([`
      class P extends AudioWorkletProcessor{
        process(inputs){
          const ch = inputs[0][0]; if(!ch) return true;
          const pcm = new Int16Array(ch.length);
          for(let i=0;i<ch.length;i++){const s=Math.max(-1,Math.min(1,ch[i]));pcm[i]=s<0?s*0x8000:s*0x7fff;}
          this.port.postMessage(pcm.buffer,[pcm.buffer]); return true;
        }
      } registerProcessor('pcm',P);`], {type: "application/javascript"}));
    await audioCtx.audioWorklet.addModule(blobURL);
    node = new AudioWorkletNode(audioCtx, "pcm");
    node.port.onmessage = e => ws.readyState === 1 && ws.send(e.data);
    audioCtx.createMediaStreamSource(stream).connect(node);
    node.connect(audioCtx.destination);

    // capture the same audio for the diarized close-out pass
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = e => e.data.size && chunks.push(e.data);
    recorder.start();
  };

  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === "transcript") addTurn(m.text, m.final, "");
    else if (m.type === "commitment") addCommitment(m);
    else if (m.type === "error") setStatus(m.message.slice(0, 40), "err");
  };
  ws.onclose = () => running && setStatus("disconnected", "err");
}

async function stopLive() {
  uiRunning(false); setStatus("finishing", "");
  try { ws?.readyState === 1 && ws.send(JSON.stringify({action: "stop"})); } catch {}
  try { node?.disconnect(); await audioCtx?.close(); } catch {}

  if (recorder && recorder.state !== "inactive") {
    const done = new Promise(r => recorder.onstop = r);
    recorder.stop(); await done;
  }
  stream?.getTracks().forEach(t => t.stop());
  try { ws?.close(); } catch {}

  if (!chunks.length) { setStatus("idle"); return; }
  $("#finalizeBar").hidden = false;
  const fd = new FormData();
  fd.append("audio", new Blob(chunks, {type: "audio/webm"}), "call.webm");
  try {
    const r = await fetch("/api/finalize", {method: "POST", body: fd});
    const d = await r.json();
    $("#finalizeBar").hidden = true;
    if (d.error) { setStatus("close-out failed", "err"); return; }
    clear(elC); commits = 0;
    $("#passLabel").textContent = "close-out pass · diarized";
    (d.commitments || []).forEach(c => addCommitment(c, true));
    setStatus(`${d.count} commitments`, "");
  } catch {
    $("#finalizeBar").hidden = true; setStatus("close-out failed", "err");
  }
}

$("#demoBtn").onclick = runDemo;
$("#liveBtn").onclick = startLive;
$("#stopBtn").onclick = () => running && (ws ? stopLive() : uiRunning(false));

// static build: no backend here, so live streaming is unavailable by design
$("#liveBtn").disabled = true;
$("#liveBtn").title = "Live streaming needs the local server + AssemblyAI key. Run it locally, or see the demo video.";


/* ------------------------------------------------------------------ present mode
   Runs the demo with timed caption overlays so a silent screen recording explains
   itself. No narrator required. */
const SCRIPT = [
  {at: 0,    ms: 4200, text: "Every sales call ends with promises. Most of them are never written down."},
  {at: 4400, ms: 4200, text: "Pact listens to the call and catches each one as it's spoken."},
  {at: 0,    ms: 0,    action: "start"},
  {at: 12000, ms: 4200, text: "There's the first: a revised MSA, due Friday. Caught the moment it was said."},
  {at: 21000, ms: 4200, text: "Streaming can't tell who's speaking — so Pact doesn't guess. It says 'attribution pending.'"},
  {at: 31000, ms: 4200, text: "It ignores requests. \"Can you share the SOC 2?\" is an ask, not a promise."},
  {at: 41000, ms: 4200, text: "Six commitments, each with an owner and a deadline, pulled from a two-minute call."},
  {at: 47000, ms: 5000, text: "When the call ends, a second pass re-runs the audio with speaker diarization."},
  {at: 53000, ms: 5200, text: "Now every promise has a real name on it. Live speed, then accurate attribution."},
];

async function present() {
  document.body.classList.add("presenting");
  const cap = $("#caption"), capT = $("#captionText");
  const show = (t, ms) => { capT.textContent = t; cap.hidden = false;
                            clearTimeout(show._h); show._h = setTimeout(() => cap.hidden = true, ms); };
  const t0 = Date.now();
  for (const step of SCRIPT) {
    const wait = step.at - (Date.now() - t0);
    if (wait > 0) await sleep(wait);
    if (step.action === "start") { runDemo(); continue; }
    show(step.text, step.ms);
  }
  await sleep(5500);
  document.body.classList.remove("presenting");
}
$("#presentBtn").onclick = present;
