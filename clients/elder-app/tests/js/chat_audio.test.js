// Voice notes in a long chat (CHAT_CONNECT_JS_TEMPLATE in elder_app.py, run for real in jsdom):
// the audio is downloaded a few at a time, not all at once, so opening a chat with many voice
// notes does not use up the gateway's database connections (issue 154). Run by
// tests/test_chat_text.py, which renders the page script into CHAT_JS.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { JSDOM } = require("jsdom");

const SRC = fs.readFileSync(process.env.CHAT_JS, "utf8");
const windows = [];
test.after(() => windows.forEach((w) => w.close()));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A chat of `count` received voice notes; `answer(id, attempt)` says how the gateway replies. */
function boot(count, answer) {
  const dom = new JSDOM('<!doctype html><div id="live-chat-messages"></div>', { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  windows.push(w);
  w.localStorage.setItem("satsandesh_my_id", "me");
  w.localStorage.setItem("sat_prefs", JSON.stringify({ lang: "te", tts: true, autoplay: false, locked: true }));
  w.__satUserId = "me";
  w.__satToken = "me";
  w.__satPrefs = { lang: "te", tts: true, autoplay: false, ui: "en", locked: true };
  w.WebSocket = class { constructor() { this.readyState = 0; } send() {} close() {} };
  let n = 0;
  w.URL.createObjectURL = () => "blob:fake-" + ++n;
  w.HTMLMediaElement.prototype.load = () => {};
  const stats = { inFlight: 0, maxInFlight: 0, attempts: {} };
  w.fetch = async (url) => {
    const id = String(url).split("/media/")[1];
    stats.attempts[id] = (stats.attempts[id] || 0) + 1;
    stats.inFlight += 1;
    stats.maxInFlight = Math.max(stats.maxInFlight, stats.inFlight);
    await wait(20);
    stats.inFlight -= 1;
    const status = answer(id, stats.attempts[id]);
    return { ok: status === 200, status, blob: async () => new w.Blob(["x"], { type: "audio/webm" }), json: async () => ({}) };
  };
  w.eval(SRC);
  w.__satCurrentContactId = "them";
  w.__satConversations = {
    them: Array.from({ length: count }, (_, i) => ({
      id: "m" + i, author_id: "them", target_id: "me", target_type: "user", kind: "voice",
      media_ref: { uri: "media:id" + i, format: "webm_opus", duration_ms: 3000 },
      text: null, transcript: "note " + i, status: "sent", renderings: [],
    })),
  };
  w.__satRenderCurrentThread();
  return { w, stats };
}

const loaded = (w) => [...w.document.querySelectorAll("audio")].filter((a) => a.src).length;
const failedButtons = (w) => [...w.document.querySelectorAll("#live-chat-messages button")].filter((b) => b.style.display === "block").length;

test("twenty voice notes are fetched three at a time, and all of them load", async () => {
  const { w, stats } = boot(20, () => 200);
  await wait(500);
  assert.equal(stats.maxInFlight <= 3, true, "at most 3 at once, saw " + stats.maxInFlight);
  assert.equal(loaded(w), 20);
});

test("a server error is tried once more, and then the note loads", async () => {
  const { w, stats } = boot(1, (id, attempt) => (attempt === 1 ? 500 : 200));
  await wait(1900);
  assert.equal(stats.attempts.id0, 2);
  assert.equal(loaded(w), 1);
});

test("a note that keeps failing shows the retry button after one more try, not a loop", async () => {
  const { w, stats } = boot(1, () => 500);
  await wait(1900);
  assert.equal(stats.attempts.id0, 2, "exactly one retry");
  assert.equal(failedButtons(w), 1);
});

test("a missing note (404) is not retried", async () => {
  const { w, stats } = boot(1, () => 404);
  await wait(300);
  assert.equal(stats.attempts.id0, 1);
  assert.equal(failedButtons(w), 1);
});

test("re-drawing the chat does not fetch the same note twice", async () => {
  const { w, stats } = boot(5, () => 200);
  await wait(400);
  w.__satRenderCurrentThread();
  w.__satRenderCurrentThread();
  await wait(200);
  assert.deepEqual(Object.values(stats.attempts), [1, 1, 1, 1, 1]);
});
