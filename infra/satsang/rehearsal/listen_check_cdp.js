// Drives an installed Chrome (headless) over the DevTools protocol with Node's built-in WebSocket (Node 22+).
// No Playwright, no Puppeteer, nothing downloaded. A headless desktop browser is NOT a phone.
//
//   node listen_check_cdp.js <page url> <seconds> [path to chrome]
//   e.g. node listen_check_cdp.js "http://127.0.0.1:8200/listen_check.html?sid=<session id>" 45
//
// Chrome is taken from the third argument, else $CHROME, else the usual Windows/Linux/macOS locations.
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const url = process.argv[2];
const seconds = parseInt(process.argv[3] || "45");
const chromePath = process.argv[4] || process.env.CHROME || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].find((p) => fs.existsSync(p));
if (!chromePath) throw new Error("no Chrome found: pass its path as the third argument or set CHROME");
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "rh-chrome-"));
const chrome = spawn(
  chromePath,
  ["--headless=new", "--remote-debugging-port=9333", `--user-data-dir=${profile}`,
   "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check", "--disable-gpu", "about:blank"],
  { stdio: "ignore" }
);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// chrome.kill() alone leaves Chrome's child processes (and the debug port) behind on Windows.
const stopChrome = async () => {
  if (process.platform === "win32") require("child_process").spawnSync("taskkill", ["/PID", String(chrome.pid), "/T", "/F"]);
  else chrome.kill();
  await sleep(1500); // Chrome's children let go of the profile folder a moment after they are killed
  try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 }); } catch {}
};
(async () => {
  let ver;
  for (let i = 0; i < 40; i++) { try { ver = await (await fetch("http://127.0.0.1:9333/json/version")).json(); break; } catch { await sleep(250); } }
  if (!ver) throw new Error("chrome did not start");
  console.log("BROWSER", ver.Browser, "| headless:", /Headless/.test(ver["User-Agent"]));
  const tgt = await (await fetch("http://127.0.0.1:9333/json/new?about:blank", { method: "PUT" })).json();
  const ws = new WebSocket(tgt.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0;
  const pend = new Map();
  const consoleLines = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); }
    else if (d.method === "Runtime.consoleAPICalled") consoleLines.push(d.params.type + ": " + d.params.args.map((a) => a.value ?? a.description).join(" "));
    else if (d.method === "Runtime.exceptionThrown") consoleLines.push("EXCEPTION: " + JSON.stringify(d.params.exceptionDetails.exception && d.params.exceptionDetails.exception.description));
  };
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result.result.value;
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Page.navigate", { url });
  await sleep(1500);
  console.log("INFO", await ev("JSON.stringify(window.__out.info)"));
  await ev("window.startPlay(); 'played'");
  await sleep(seconds * 1000);
  console.log("ELEMENT NOW", await ev("(() => { const a = document.getElementById('satsang-audio'); return JSON.stringify({paused: a.paused, ended: a.ended, ct: a.currentTime, rs: a.readyState, vis: document.visibilityState, buffered: a.buffered.length ? [a.buffered.start(0), a.buffered.end(0)] : null, duration: a.duration}); })()"));
  console.log("RESUME TRY", await ev("document.getElementById('satsang-audio').play().then(() => 'play() resolved', e => 'play() rejected: ' + e.name)"));
  await sleep(4000);
  console.log("ELEMENT AFTER RESUME TRY", await ev("(() => { const a = document.getElementById('satsang-audio'); return JSON.stringify({paused: a.paused, ct: a.currentTime}); })()"));
  const out = JSON.parse(await ev("JSON.stringify(window.__out)"));
  console.log("HLS.JS EVENTS (ms since page load)");
  for (const e of out.events) console.log(" ", JSON.stringify(e));
  console.log("AUDIO ELEMENT EVENTS");
  for (const e of out.audio) console.log(" ", JSON.stringify(e));
  console.log("PLAYER STATUS (satsang_player.js status(), changes only)");
  let last = "";
  for (const [t, s] of out.status) if (s !== last) { console.log(" ", t, s); last = s; }
  console.log("DECODED OUTPUT [ms, rms of what the element outputs, currentTime, AudioContext state], every 3rd second");
  for (const r of out.rms.filter((_, i) => i % 3 === 0)) console.log(" ", JSON.stringify(r));
  console.log("CONSOLE");
  for (const l of consoleLines.slice(0, 20)) console.log(" ", l.slice(0, 220));
  await stopChrome();
  process.exit(0);
})().catch(async (e) => { console.log("DRIVER ERROR", e.message); await stopChrome(); process.exit(1); });
