// Run in the browser's JS console on a page served by the gate stack's Caddy (same origin, so no CORS):
//   http://<host>:18500/health
//
// What it does, exactly: fetches a staged Telugu WAV from the media store, plays it into a
// MediaStreamDestination and records THAT with the browser's own MediaRecorder (default mimeType, as
// the elder app does: audio/webm;codecs=opus), then uploads the recording as `webm_opus` and sends it
// as a voice note. The result is a genuine Chrome WebM/Opus file. IT IS NOT A MICROPHONE RECORDING:
// the built-in browser had no `navigator.mediaDevices` (a plain-HTTP, non-localhost origin has none),
// so the speech source is the synthesized Piper sentence, not a person.
//
// Also seen on that origin: `crypto.randomUUID` does not exist either (secure contexts only), hence
// the manual uuid().
//
// Fill in ELDER (the elder's legacy bearer token = user id), RECV (the receiver's user id) and WAV_ID
// (what stage_wav.py printed).
const ELDER = "<elder user id>";
const RECV = "<receiver user id>";
const WAV_ID = "<media id printed by stage_wav.py>";

const uuid = () =>
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });

const wavRes = await fetch("/media/" + WAV_ID, { headers: { Authorization: "Bearer " + ELDER } });
const ctx = new AudioContext();
const buf = await ctx.decodeAudioData(await wavRes.arrayBuffer());
const dest = ctx.createMediaStreamDestination();
const src = ctx.createBufferSource();
src.buffer = buf;
src.connect(dest);
const rec = new MediaRecorder(dest.stream);
const chunks = [];
rec.ondataavailable = (e) => chunks.push(e.data);
const stopped = new Promise((r) => (rec.onstop = r));
rec.start();
src.start();
src.onended = () => setTimeout(() => rec.stop(), 300);
await stopped;

const blob = new Blob(chunks, { type: rec.mimeType });
const head = Array.from(new Uint8Array(await blob.slice(0, 4).arrayBuffer()))
  .map((b) => b.toString(16).padStart(2, "0"))
  .join(""); // 1a45dfa3 = EBML, i.e. a real WebM container
const durationMs = Math.round(buf.duration * 1000);
const up = await fetch("/media?format=webm_opus&duration_ms=" + durationMs, {
  method: "POST",
  headers: { Authorization: "Bearer " + ELDER },
  body: blob,
});
const media = await up.json();
const send = await fetch("/messages", {
  method: "POST",
  headers: { Authorization: "Bearer " + ELDER, "Content-Type": "application/json" },
  body: JSON.stringify({
    client_msg_id: uuid(),
    target_type: "user",
    target_id: RECV,
    kind: "voice",
    source_lang: "te",
    media_ref: { uri: media.uri, format: media.format, duration_ms: durationMs },
  }),
});
({ mimeType: rec.mimeType, bytes: blob.size, first4_hex: head, upload: up.status, media, send: send.status, ack: await send.json() });
