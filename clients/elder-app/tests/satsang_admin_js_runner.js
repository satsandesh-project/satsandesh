// Runs the satsang admin page's browser scripts (the exact strings the page hands to
// rx.call_script) against a fake DOM, a fake sessionStorage and a scripted fetch, in one shared
// context so one step can leave something for the next. Read by tests/test_satsang_admin.py.
//
//   stdin  : {"steps": [<js string> | {"fire_timers": true}], "dom": {id: {value, textContent, dataset}},
//             "storage": {...} (sessionStorage), "local": {...} (localStorage), "fetches": [{"status": 200, "body": {...}} | {"throw": true}],
//             "clipboard_fails": false}
//   stdout : {"results": [...], "calls": [...], "dom": {...}, "store": {...}, "clipboard": ..., "timers": [ms]}
//
// Set the TZ environment variable to choose the time zone the browser would be in.
const vm = require("vm");
const fs = require("fs");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const calls = [];
const timers = [];
const clip = { text: null, fail: !!input.clipboard_fails };

const dom = {};
for (const [id, spec] of Object.entries(input.dom || {})) {
  dom[id] = Object.assign({ id, value: "", textContent: "", dataset: {}, style: {} }, spec);
  dom[id].dataset = Object.assign({}, spec.dataset || {});
}
const store = Object.assign({}, input.storage || {});
const localStore = Object.assign({}, input.local || {});
const queue = (input.fetches || []).slice();

const ctx = {
  document: { getElementById: (id) => dom[id] || null },
  sessionStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
  },
  localStorage: {
    getItem: (k) => (k in localStore ? localStore[k] : null),
    setItem: (k, v) => {
      localStore[k] = String(v);
    },
    removeItem: (k) => {
      delete localStore[k];
    },
  },
  navigator: {
    clipboard: {
      writeText: async (t) => {
        if (clip.fail) throw new Error("denied");
        clip.text = t;
      },
    },
  },
  setTimeout: (fn, ms) => {
    timers.push({ fn, ms });
    return timers.length;
  },
  clearTimeout: () => {},
  fetch: async (url, opts) => {
    opts = opts || {};
    calls.push({
      url,
      method: opts.method || "GET",
      headers: opts.headers || {},
      body: opts.body ? JSON.parse(opts.body) : null,
    });
    const r = queue.shift();
    if (!r) throw new Error("no scripted response left for " + url);
    if (r.throw) throw new Error("network down");
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      json: async () => {
        if (r.body === undefined) throw new Error("no body");
        return r.body;
      },
    };
  },
};
ctx.window = ctx;
vm.createContext(ctx);

(async () => {
  const results = [];
  for (const step of input.steps) {
    if (step && step.fire_timers) {
      for (const t of timers.splice(0)) t.fn();
      results.push(null);
      continue;
    }
    results.push(await vm.runInContext(step, ctx));
  }
  process.stdout.write(
    JSON.stringify({
      results,
      calls,
      dom,
      store,
      local: localStore,
      clipboard: clip.text,
      timers: timers.map((t) => t.ms),
    })
  );
})().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String((e && e.stack) || e) }));
  process.exit(1);
});
