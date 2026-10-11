// The bhajan rooms SCREEN (assets/bhajan_rooms.js) run for real in jsdom, against a small fake of
// the gateway's /rooms API and a fake LiveKit client. It checks what a person would see and what the
// app sends, not the layout: that the right buttons appear for the server's answer, that a tap sends
// the right request, that only the lead's microphone is switched on and it is switched off again
// on a hand-over, and that a failure is shown.
//
//   cd tests/js && npm install && node --test .
// Skipped when jsdom is not installed.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

let JSDOM;
try {
  ({ JSDOM } = require("jsdom"));
} catch (e) {
  JSDOM = null;
}

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "..", "assets", "bhajan_rooms.js"), "utf8");
const TEXTS = {
  en: new Proxy({}, { get: (_, key) => (typeof key === "string" ? "T:" + key : undefined) }),
  te: new Proxy({}, { get: (_, key) => (typeof key === "string" ? "TE:" + key : undefined) }),
};
const COLORS = { ink: "#000", muted_ink: "#555", deep_green: "#030", green_tint: "#efe", green_ink: "#020", card_cream: "#fff", warm_border: "#ddd" };

/** A tiny gateway: rooms, a queue, one lead, the same rules as app/rooms.py. */
class FakeGateway {
  constructor() {
    this.calls = [];
    this.me = "me";
    this.names = { me: "Me", anand: "Anand", bhavya: "Bhavya" };
    this.room = { id: "r1", circle_id: "c1", title: "Evening bhajans", status: "open", livekit_room: "room-r1", lead: null, waiting_count: 0 };
    this.entries = []; // {user_id, state}
    this.moderator = false;
    this.fail = {}; // "METHOD path" -> status
    this.tokenCanPublish = false;
  }
  lead() { return this.entries.find((e) => e.state === "leading") || null; }
  waiting() { return this.entries.filter((e) => e.state === "waiting"); }
  person(e) { return e ? { user_id: e.user_id, name: this.names[e.user_id] } : null; }
  state() {
    const lead = this.lead();
    const waiting = this.waiting();
    const mine = [...this.entries].reverse().find((e) => e.user_id === this.me) || null;
    const isLead = !!(lead && lead.user_id === this.me);
    const first = !lead && waiting.length && waiting[0].user_id === this.me;
    return {
      room: Object.assign({}, this.room, { lead: this.person(lead), waiting_count: waiting.length }),
      lead: this.person(lead),
      queue: waiting.map((e, i) => ({ user_id: e.user_id, name: this.names[e.user_id], position: i + 1, state: "waiting" })),
      you: {
        state: mine ? mine.state : null,
        position: mine && mine.state === "waiting" ? waiting.indexOf(mine) + 1 : null,
        is_lead: isLead,
        can_take_floor: !!((lead || waiting.length) && (isLead || this.moderator || first)),
        can_manage: this.moderator,
      },
    };
  }
  handler() {
    return async (url, init) => {
      const method = (init && init.method) || "GET";
      const p = url.replace("http://gw", "");
      this.calls.push(method + " " + p);
      const forced = this.fail[method + " " + p];
      if (forced) return { ok: false, status: forced, json: async () => ({ detail: "x" }) };
      const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
      if (method === "GET" && p === "/rooms") return reply({ items: this.noRooms ? [] : [this.room] });
      if (method === "GET" && p === "/circles") return reply([{ id: "c1", name: "Satsang" }]);
      if (method === "GET" && p === "/rooms/r1") return reply(this.state());
      if (method === "POST" && p === "/rooms/r1/token") return reply({ token: "tok", url: "wss://x/livekit", identity: this.me, can_publish: this.tokenCanPublish });
      if (method === "POST" && p === "/rooms/r1/queue") {
        if (!this.entries.some((e) => e.user_id === this.me && ["waiting", "leading"].includes(e.state))) this.entries.push({ user_id: this.me, state: "waiting" });
        return reply(this.state());
      }
      if (method === "DELETE" && p === "/rooms/r1/queue") {
        const mine = this.entries.find((e) => e.user_id === this.me && e.state === "waiting");
        if (mine) mine.state = "done";
        return reply(this.state());
      }
      if (method === "POST" && p === "/rooms/r1/next") {
        const lead = this.lead();
        if (lead) lead.state = "done";
        const next = this.waiting()[0];
        if (next) next.state = "leading";
        return reply(this.state());
      }
      if (method === "POST" && p.startsWith("/rooms/r1/queue/") && p.endsWith("/skip")) {
        const who = p.split("/")[4];
        const entry = this.waiting().find((e) => e.user_id === who);
        if (entry) entry.state = "removed";
        return reply(this.state());
      }
      if (method === "POST" && p === "/rooms/r1/close") { this.room.status = "closed"; return reply(this.state()); }
      if (method === "POST" && p === "/rooms") return reply(Object.assign({}, this.room), 201);
      return reply({ detail: "no" }, 404);
    };
  }
}

class FakeLivekit {
  static install(window, log) {
    class Room {
      constructor() { this.handlers = {}; this.remoteParticipants = new Map(); this.canPlaybackAudio = Room.canPlayback; this.localParticipant = { name: "Me", setMicrophoneEnabled: async (on) => { log.push("mic:" + on); } }; log.push("new Room"); }
      on(name, fn) { this.handlers[name] = fn; return this; }
      async connect(url, token) { log.push("connect " + url + " " + token); }
      async startAudio() { log.push("startAudio"); }
      async disconnect() { log.push("disconnect"); }
    }
    Room.canPlayback = true;
    window.LivekitClient = { Room, RoomEvent: { TrackSubscribed: "ts", TrackUnsubscribed: "tu", Disconnected: "d", AudioPlaybackStatusChanged: "a" } };
  }
}

const windows = [];
// The screen polls the room on a timer; closing each window stops it so the test run can end.
test.after(() => windows.forEach((w) => w.close()));

const tick = (ms = 120) => new Promise((resolve) => setTimeout(resolve, ms));

async function boot(gateway, options = {}) {
  const dom = new JSDOM('<!doctype html><div id="bhajan-root"></div>', { url: "http://localhost/bhajans", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  windows.push(window);
  const log = [];
  window.fetch = gateway.handler();
  window.localStorage.setItem("satsandesh_my_id", gateway.me);
  window.localStorage.setItem("sat_prefs", JSON.stringify({ lang: options.lang || "en" }));
  window.confirm = () => options.confirm !== false;
  FakeLivekit.install(window, log);
  window.eval(SOURCE);
  window.SatBhajan.start({ gateway: "http://gw", colors: COLORS, texts: TEXTS, pollMs: 40 });
  await tick();
  return { window, doc: window.document, log };
}

const buttons = (doc) => [...doc.querySelectorAll("button")].map((b) => b.textContent);
const click = async (doc, label) => {
  const b = [...doc.querySelectorAll("button")].find((x) => x.textContent === label);
  assert.ok(b, "no button '" + label + "' among: " + buttons(doc).join(" | "));
  b.click();
  await tick();
};

test("jsdom is installed", { skip: JSDOM ? false : "jsdom not installed (npm install in tests/js)" }, () => {});

const run = JSDOM ? test : test.skip;

run("the list shows each open room with a Join button, the start form and Back", async () => {
  const gw = new FakeGateway();
  const { doc } = await boot(gw);
  assert.ok(doc.body.textContent.includes("Evening bhajans"));
  assert.deepEqual(buttons(doc), ["T:join", "T:start_button", "T:back"]);
});

run("with no open room it says so", async () => {
  const gw = new FakeGateway();
  gw.noRooms = true;
  const { doc } = await boot(gw);
  assert.ok(doc.body.textContent.includes("T:no_rooms"));
});

run("joining asks for a token, connects, and shows the room with 'raise hand'", async () => {
  const gw = new FakeGateway();
  const { doc, log } = await boot(gw);
  await click(doc, "T:join");
  assert.ok(gw.calls.includes("POST /rooms/r1/token"));
  assert.ok(log.some((l) => l.startsWith("connect wss://x/livekit tok")));
  assert.ok(log.includes("startAudio"));
  assert.ok(doc.body.textContent.includes("T:nobody_singing"));
  assert.deepEqual(buttons(doc).filter((t) => t !== "T:leave"), ["T:raise"]);
  assert.ok(!log.includes("mic:true"), "nobody has a live microphone yet");
});

run("raising a hand sends it and the button becomes 'lower hand' with the place in line", async () => {
  const gw = new FakeGateway();
  gw.entries.push({ user_id: "anand", state: "leading" });
  const { doc } = await boot(gw);
  await click(doc, "T:join");
  await click(doc, "T:raise");
  assert.ok(gw.calls.includes("POST /rooms/r1/queue"));
  assert.ok(buttons(doc).includes("T:lower"));
  assert.ok(doc.body.textContent.includes("T:your_place"));
  await click(doc, "T:lower");
  assert.ok(gw.calls.includes("DELETE /rooms/r1/queue"));
  assert.ok(buttons(doc).includes("T:raise"));
});

run("when the server makes me the lead my microphone goes on, once, and I get 'I am done'", async () => {
  const gw = new FakeGateway();
  const { doc, log } = await boot(gw);
  await click(doc, "T:join");
  await click(doc, "T:raise");
  assert.ok(buttons(doc).includes("T:take"), "first in line with an empty floor may take it");
  await click(doc, "T:take");
  await tick(200);
  assert.deepEqual(buttons(doc).filter((t) => t !== "T:leave"), ["T:pass"]);
  assert.equal(log.filter((l) => l === "mic:true").length, 1, "the microphone is switched on once, not on every poll");
  assert.ok(doc.body.textContent.includes("T:you_are_live"));
});

run("a hand-over switches my microphone off", async () => {
  const gw = new FakeGateway();
  gw.entries.push({ user_id: "bhavya", state: "waiting" });
  const { doc, log } = await boot(gw);
  await click(doc, "T:join");
  await click(doc, "T:raise");
  gw.entries.find((e) => e.user_id === "me").state = "leading"; // the server promoted me
  await tick(200);
  assert.ok(log.includes("mic:true"));
  await click(doc, "T:pass");
  await tick(200);
  assert.ok(log.includes("mic:false"), "the old lead's microphone is turned off");
  assert.equal(gw.lead().user_id, "bhavya");
});

run("a listener never has a microphone switched on", async () => {
  const gw = new FakeGateway();
  gw.entries.push({ user_id: "anand", state: "leading" });
  const { doc, log } = await boot(gw);
  await click(doc, "T:join");
  await tick(250);
  assert.ok(!log.some((l) => l.startsWith("mic:")), log.join(","));
  assert.ok(doc.body.textContent.includes("Anand"));
});

run("a moderator sees skip buttons, can hand over, and closes the room after confirming", async () => {
  const gw = new FakeGateway();
  gw.moderator = true;
  gw.entries.push({ user_id: "anand", state: "leading" }, { user_id: "bhavya", state: "waiting" });
  const { doc } = await boot(gw);
  await click(doc, "T:join");
  assert.ok(buttons(doc).includes("T:skip"));
  assert.ok(buttons(doc).includes("T:handover"));
  await click(doc, "T:skip");
  assert.ok(gw.calls.includes("POST /rooms/r1/queue/bhavya/skip"));
  await click(doc, "T:close_room");
  assert.ok(gw.calls.includes("POST /rooms/r1/close"));
});

run("closing is not sent if the moderator says no", async () => {
  const gw = new FakeGateway();
  gw.moderator = true;
  const { doc } = await boot(gw, { confirm: false });
  await click(doc, "T:join");
  await click(doc, "T:close_room");
  assert.ok(!gw.calls.includes("POST /rooms/r1/close"));
});

run("an ordinary member who tries to start a room is told only a moderator can", async () => {
  const gw = new FakeGateway();
  gw.fail["POST /rooms"] = 403;
  const { doc } = await boot(gw);
  const input = doc.querySelector("input");
  input.value = "My room";
  doc.querySelector("select").dispatchEvent(new doc.defaultView.Event("change"));
  await tick();
  await click(doc, "T:start_button");
  assert.ok(doc.body.textContent.includes("T:err_forbidden"), doc.body.textContent);
});

run("a failed request shows the right message, and a lost connection is not reported as a refusal", async () => {
  const gw = new FakeGateway();
  gw.fail["POST /rooms/r1/token"] = 503;
  const { doc } = await boot(gw);
  await click(doc, "T:join");
  assert.ok(doc.body.textContent.includes("T:err_audio"));

  const gw2 = new FakeGateway();
  const { doc: doc2 } = await boot(gw2);
  gw2.handler = () => async () => { throw new Error("offline"); };
  doc2.defaultView.fetch = gw2.handler();
  await click(doc2, "T:join");
  assert.ok(doc2.body.textContent.includes("T:err_network"), doc2.body.textContent);
});

run("a closed room is shown as closed and the buttons go away", async () => {
  const gw = new FakeGateway();
  const { doc } = await boot(gw);
  await click(doc, "T:join");
  gw.room.status = "closed";
  await tick(150);
  assert.ok(doc.body.textContent.includes("T:err_closed"));
  assert.ok(!buttons(doc).includes("T:raise"));
});

run("leaving disconnects the audio and returns to the list", async () => {
  const gw = new FakeGateway();
  const { doc, log } = await boot(gw);
  await click(doc, "T:join");
  await click(doc, "T:leave");
  assert.ok(log.includes("disconnect"));
  assert.ok(buttons(doc).includes("T:join"));
});

run("a person who chose Telugu sees the Telugu words", async () => {
  const gw = new FakeGateway();
  const { doc } = await boot(gw, { lang: "te" });
  assert.ok(buttons(doc).includes("TE:join"));
});

run("a browser that keeps audio silent gets a 'tap to hear' button", async () => {
  const gw = new FakeGateway();
  const { doc, window } = await boot(gw);
  window.LivekitClient.Room.canPlayback = false;
  await click(doc, "T:join");
  assert.ok(buttons(doc).includes("T:tap_to_hear"));
});
