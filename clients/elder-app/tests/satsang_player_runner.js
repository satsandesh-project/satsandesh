// Runs assets/satsang_player.js (the satsang listener's player) against a fake audio element, a fake
// `Hls` class and a clock the test controls: nothing sleeps, no network. Read by
// tests/test_satsang_player.py.
//
//   stdin  : {"hls": "supported" | "unsupported", "native": bool, "no_audio": bool, "no_url": bool,
//             "play_rejects": null | "NotAllowedError", "steps": [step, ...]}
//   step   : {"call": "play" | "stop" | "status"} | {"audio": "playing" | "waiting" | "stalled" | "error"}
//            | {"hls": ["manifestLoadError" | ..., {...data}]} | {"advance_s": 9} | {"fire_timers": true}
//   stdout : {"results": [status snapshot | null per step], "log": [...], "timers": [ms], "audio": {...}}
const vm = require("vm");
const fs = require("fs");
const path = require("path");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const log = [];
const timers = [];
let nowMs = 1_000_000;
const handlers = {};
const hlsInstances = [];

const audio = {
  dataset: input.no_url ? {} : { url: "https://stream.example/hls/abc.m3u8" },
  src: "",
  paused: true,
  addEventListener(type, fn) {
    (handlers[type] = handlers[type] || []).push(fn);
  },
  removeEventListener(type, fn) {
    handlers[type] = (handlers[type] || []).filter((f) => f !== fn);
  },
  play() {
    log.push("audio.play");
    if (input.play_rejects) {
      const err = new Error("blocked");
      err.name = input.play_rejects;
      return Promise.reject(err);
    }
    return Promise.resolve();
  },
  pause() {
    log.push("audio.pause");
  },
  load() {
    log.push("audio.load");
  },
  removeAttribute(name) {
    log.push("audio.removeAttribute:" + name);
    if (name === "src") this.src = "";
  },
  canPlayType(type) {
    return input.native && type === "application/vnd.apple.mpegurl" ? "maybe" : "";
  },
};

class FakeHls {
  static isSupported() {
    return input.hls === "supported";
  }
  constructor(config) {
    this.config = config;
    this.listeners = {};
    this.destroyed = false;
    hlsInstances.push(this);
    log.push("Hls.new");
  }
  on(event, fn) {
    this.listeners[event] = fn;
  }
  loadSource(url) {
    log.push("hls.loadSource:" + url);
  }
  attachMedia(el) {
    log.push("hls.attachMedia:" + (el === audio));
  }
  startLoad() {
    log.push("hls.startLoad");
  }
  recoverMediaError() {
    log.push("hls.recoverMediaError");
  }
  destroy() {
    this.destroyed = true;
    log.push("hls.destroy");
  }
}
FakeHls.Events = { MANIFEST_PARSED: "hlsManifestParsed", ERROR: "hlsError" };
FakeHls.ErrorTypes = { NETWORK_ERROR: "networkError", MEDIA_ERROR: "mediaError", OTHER_ERROR: "otherError" };
FakeHls.ErrorDetails = {
  MANIFEST_LOAD_ERROR: "manifestLoadError",
  MANIFEST_LOAD_TIMEOUT: "manifestLoadTimeOut",
  MANIFEST_PARSING_ERROR: "manifestParsingError",
};

const ctx = {
  document: { getElementById: (id) => (!input.no_audio && id === "satsang-audio" ? audio : null) },
  Hls: FakeHls,
  setTimeout: (fn, ms) => {
    timers.push({ fn, ms });
    return timers.length;
  },
  clearTimeout: () => {},
  Date: { now: () => nowMs },
  console: { log: () => {}, warn: () => {}, error: () => {} },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "assets", "satsang_player.js"), "utf8"), ctx);

const fire = (type) => (handlers[type] || []).slice().forEach((fn) => fn({ type }));
const snapshot = () => JSON.parse(JSON.stringify(ctx.satsang.status()));

(async () => {
  const results = [];
  for (const step of input.steps) {
    if (step.call === "play") ctx.satsang.play();
    else if (step.call === "stop") ctx.satsang.stop();
    else if (step.audio) fire(step.audio);
    else if (step.hls) {
      const hls = hlsInstances[hlsInstances.length - 1];
      const [detailsOrKind, data] = step.hls;
      if (detailsOrKind === "manifestParsed") hls.listeners[FakeHls.Events.MANIFEST_PARSED]("x", {});
      else hls.listeners[FakeHls.Events.ERROR]("x", data);
    } else if (step.advance_s) nowMs += step.advance_s * 1000;
    else if (step.fire_timers) for (const t of timers.splice(0)) t.fn();
    await Promise.resolve(); // let a rejected play() promise settle
    await Promise.resolve();
    results.push(step.call === "status" || step.snapshot ? snapshot() : null);
  }
  process.stdout.write(
    JSON.stringify({
      results,
      log,
      timers: timers.map((t) => t.ms),
      final: snapshot(),
      hls: hlsInstances.map((h) => ({ destroyed: h.destroyed })),
      audio_src: audio.src,
    })
  );
})().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exit(1);
});
