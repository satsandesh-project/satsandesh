// Typed chat messages in the receiver's chat (CHAT_CONNECT_JS_TEMPLATE in elder_app.py, run for
// real in jsdom): the reader sees the translation, and can always switch to what was typed, or to
// the English version. Run by tests/test_chat_text.py, which renders the page script into CHAT_JS
// and skips this when node or jsdom is not installed.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { JSDOM } = require("jsdom");

const SRC = fs.readFileSync(process.env.CHAT_JS, "utf8");
const windows = [];
test.after(() => windows.forEach((w) => w.close()));
const tick = (ms = 60) => new Promise((resolve) => setTimeout(resolve, ms));

function boot({ lang, message }) {
  const dom = new JSDOM('<!doctype html><div id="live-chat-messages"></div>', { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  windows.push(w);
  w.localStorage.setItem("satsandesh_my_id", "me");
  w.localStorage.setItem("sat_prefs", JSON.stringify({ lang, tts: true, autoplay: false, locked: true }));
  w.__satUserId = "me";
  w.__satToken = "me";
  w.__satPrefs = { lang, tts: true, autoplay: false, ui: "en", locked: true };
  w.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
  w.URL.createObjectURL = () => "blob:fake";
  w.HTMLMediaElement.prototype.load = () => {};
  w.fetch = async () => ({ ok: true, status: 200, blob: async () => new w.Blob(["x"], { type: "audio/wav" }), json: async () => ({}) });
  w.eval(SRC);
  w.__satCurrentContactId = "them";
  w.__satConversations = { them: [Object.assign({ id: "m1", author_id: "them", target_id: "me", target_type: "user", kind: "text", media_ref: null, transcript: null, status: "sent", renderings: [] }, message)] };
  w.__satRenderCurrentThread();
  return w;
}

const shown = (w) => w.document.getElementById("live-chat-messages").textContent.replace(/\s+/g, " ").trim();
const labels = (w) => [...w.document.querySelectorAll("#live-chat-messages button")].map((b) => b.textContent).filter((t) => /original|English|translation/i.test(t));
async function press(w, label) {
  const b = [...w.document.querySelectorAll("#live-chat-messages button")].find((x) => x.textContent === label);
  assert.ok(b, "no '" + label + "' among: " + labels(w).join(" | "));
  b.click();
  await tick();
}

const TE = { language: "te", text: "తెలుగు అనువాదం", audio: { uri: "media:aaaa", format: "wav_pcm16", duration_ms: 2000 }, degraded_reason: null };
const EN = { language: "en", text: "English version", audio: null, degraded_reason: "text_only" };

test("a Telugu reader sees the translation, with 'Show original' and 'Show English'", async () => {
  const w = boot({ lang: "te", message: { text: "namaste in Hindi", renderings: [TE, EN] } });
  assert.ok(shown(w).includes("తెలుగు అనువాదం"));
  assert.ok(!shown(w).includes("namaste in Hindi"));
  assert.deepEqual(labels(w), ["Show original", "Show English"]);
});

test("'Show original' shows exactly what the sender typed, and a way back", async () => {
  const w = boot({ lang: "te", message: { text: "नमस्ते, आप कैसे हैं?", renderings: [TE, EN] } });
  await press(w, "Show original");
  assert.ok(shown(w).includes("नमस्ते, आप कैसे हैं?"));
  assert.ok(shown(w).includes("Original message"));
  assert.ok(!shown(w).includes("తెలుగు అనువాదం"));
  assert.deepEqual(labels(w), ["Show translation", "Show English"]);
  await press(w, "Show translation");
  assert.ok(shown(w).includes("తెలుగు అనువాదం"));
});

test("'Show English' shows the English version", async () => {
  const w = boot({ lang: "te", message: { text: "नमस्ते", renderings: [TE, EN] } });
  await press(w, "Show English");
  assert.ok(shown(w).includes("English version"));
  assert.deepEqual(labels(w), ["Show translation", "Show original"]);
});

test("an English reader of a Telugu message sees English, and can see what was typed", async () => {
  const w = boot({ lang: "en", message: { text: "నమస్కారం", renderings: [EN] } });
  assert.ok(shown(w).includes("English version"));
  assert.ok(!shown(w).includes("No audio for this message"), "a typed message does not need an audio note");
  assert.deepEqual(labels(w), ["Show original"]);
  await press(w, "Show original");
  assert.ok(shown(w).includes("నమస్కారం"));
  assert.deepEqual(labels(w), ["Show translation"]);
});

test("a message written in English for a Telugu reader has only 'Show original'", async () => {
  const w = boot({ lang: "te", message: { text: "Good morning", renderings: [TE] } });
  assert.deepEqual(labels(w), ["Show original"]);
  await press(w, "Show original");
  assert.ok(shown(w).includes("Good morning"));
});

test("a message in the reader's own language is shown as typed, with no buttons", async () => {
  const w = boot({ lang: "te", message: { text: "మీ పేరు ఏమిటి?", renderings: [] } });
  assert.ok(shown(w).includes("మీ పేరు ఏమిటి?"));
  assert.deepEqual(labels(w), []);
});

test("the sender sees their own typed text, with no buttons", async () => {
  const w = boot({ lang: "te", message: { author_id: "me", target_id: "them", text: "My own words", renderings: [TE] } });
  assert.ok(shown(w).includes("My own words"));
  assert.deepEqual(labels(w), []);
});

test("a voice note is unchanged: one 'Show English' swap for a Telugu reader, nothing for an English reader", async () => {
  const media = { uri: "media:bbbb", format: "webm_opus", duration_ms: 3000 };
  const te = boot({ lang: "te", message: { kind: "voice", text: null, transcript: "hello", media_ref: media, renderings: [TE, EN] } });
  assert.deepEqual(labels(te), ["Show English"]);
  const en = boot({ lang: "en", message: { kind: "voice", text: null, transcript: "hello", media_ref: media, renderings: [EN] } });
  assert.deepEqual(labels(en), []);
});
