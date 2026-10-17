/*
 * Bhajan rooms (elder app, Weeks 9 and 10). One file, two halves.
 *
 * 1. LOGIC (pure functions, no DOM): which buttons a person sees, when their microphone should be on,
 *    what to say for an error. These are exported for Node (tests/js/bhajan_rooms.test.js) and decide
 *    nothing the SERVER has not already decided: the gateway says who may take the floor
 *    (`you.can_take_floor`), who leads, and whether a token may publish. The app only draws that.
 * 2. SCREEN: the room list and the room itself, built with plain DOM calls and the gateway's /rooms
 *    API, with LiveKit's browser library (vendored as /livekit-client.umd.min.js, loaded only here)
 *    carrying the audio.
 *
 * Only the lead's microphone is ever live, and that is enforced by the gateway (the join token and
 * a permission change on every hand-over). The app turns the microphone on only for the lead, but
 * if it got that wrong the server would still not let the audio through.
 */
(function (root) {
  "use strict";

  // ---------------------------------------------------------------------------------------------
  // 1. Logic
  // ---------------------------------------------------------------------------------------------

  /** The buttons to show, in order, from the server's `you` block and the current lead. */
  function buttonsFor(you, lead) {
    if (!you) return [];
    const out = [];
    if (you.is_lead) {
      out.push("pass");
    } else if (you.can_take_floor) {
      // A moderator can hand over from someone else's turn; otherwise it is "take the next bhajan".
      out.push(lead ? "handover" : "take");
    }
    if (!you.is_lead) out.push(you.state === "waiting" ? "lower" : "raise");
    return out;
  }

  /** "on", "off" or "none": what to do to this person's microphone, given the server's word. */
  function micPlan(you, micOn) {
    const lead = !!(you && you.is_lead);
    if (lead && !micOn) return "on";
    if (!lead && micOn) return "off";
    return "none";
  }

  /**
   * True when the server says this person leads but the token they joined with could not publish
   * (they became the lead just before connecting). The microphone would be refused, so the app joins
   * again with a fresh token. A token that can publish is short-lived on purpose, so a token is never
   * kept and reused: every join and every rejoin asks the gateway for a new one.
   */
  function needsFreshToken(you, tokenCanPublish) {
    return !!(you && you.is_lead && tokenCanPublish === false);
  }

  /** Which message to show for a failed request. */
  function errorKey(status) {
    if (status === 0) return "err_network";
    if (status === 401) return "err_signin";
    if (status === 403) return "err_forbidden";
    if (status === 404) return "err_notfound";
    if (status === 409) return "err_closed";
    if (status === 502 || status === 503) return "err_audio";
    return "err_generic";
  }

  /** "You are number 3 in line": the text with the number put in. */
  function fill(text, values) {
    return String(text).replace(/\{(\w+)\}/g, function (_, name) {
      return values && name in values ? String(values[name]) : "";
    });
  }

  /** The people to list as 'in the room': everyone LiveKit says is connected, by name. */
  function hereNames(localName, remoteNames) {
    const names = [localName].concat(remoteNames).filter(Boolean);
    return Array.from(new Set(names)).sort(function (a, b) {
      return a.localeCompare(b);
    });
  }

  /** A short signature of what a list shows, so the screen is rebuilt only when it changed. */
  function signature(parts) {
    return JSON.stringify(parts);
  }

  const logic = {
    buttonsFor: buttonsFor,
    micPlan: micPlan,
    needsFreshToken: needsFreshToken,
    errorKey: errorKey,
    fill: fill,
    hereNames: hereNames,
    signature: signature,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = logic;
    return;
  }

  // ---------------------------------------------------------------------------------------------
  // 2. Screen
  // ---------------------------------------------------------------------------------------------

  const POLL_MS = 2000;
  const LK_SRC = "/livekit-client.umd.min.js";

  let cfg = null; // { gateway, colors, texts: {en:{}, te:{}} }
  let lang = "en";
  let rootEl = null;
  let pollTimer = null;
  let current = null; // { id, room, state, micOn, view, tokenCanPublish, audioLost, joinedAt, rejoining }
  const REJOIN_MIN_MS = 5000; // never rejoin more often than this, whatever the server says

  function T(key, values) {
    const table = (cfg.texts[lang] || cfg.texts.en);
    return fill(table[key] !== undefined ? table[key] : cfg.texts.en[key] || key, values);
  }

  function myId() {
    try {
      return localStorage.getItem("satsandesh_my_id") || "";
    } catch (e) {
      return "";
    }
  }

  function chosenLanguage() {
    try {
      const prefs = JSON.parse(localStorage.getItem("sat_prefs") || "{}");
      return prefs.lang === "te" ? "te" : "en";
    } catch (e) {
      return "en";
    }
  }

  async function api(path, method, body) {
    try {
      const resp = await fetch(cfg.gateway + path, {
        method: method || "GET",
        headers: Object.assign(
          { Authorization: "Bearer " + myId() },
          body ? { "Content-Type": "application/json" } : {}
        ),
        body: body ? JSON.stringify(body) : undefined,
      });
      let data = null;
      try {
        data = await resp.json();
      } catch (e) {
        data = null;
      }
      return { ok: resp.ok, status: resp.status, data: data };
    } catch (e) {
      return { ok: false, status: 0, data: null };
    }
  }

  // ---- tiny DOM helpers ----------------------------------------------------------------------

  function el(tag, style, children) {
    const node = document.createElement(tag);
    if (style) Object.assign(node.style, style);
    (children || []).forEach(function (child) {
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function text(value, style) {
    const node = el("div", style || {});
    node.textContent = value;
    return node;
  }

  function bigButton(label, onClick, kind) {
    const c = cfg.colors;
    const primary = kind === "primary";
    const danger = kind === "danger";
    const b = el("button", {
      width: "100%",
      minHeight: "68px",
      fontSize: "22px",
      fontWeight: "700",
      borderRadius: "16px",
      cursor: "pointer",
      margin: "8px 0",
      padding: "10px 16px",
      border: "2px solid " + (danger ? "#8A3E0F" : c.deep_green),
      background: primary ? c.deep_green : danger ? "#FBEFE6" : c.green_tint,
      color: primary ? c.card_cream : danger ? "#8A3E0F" : c.green_ink,
    });
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", function () {
      onClick(b);
    });
    return b;
  }

  function note(message, kind) {
    const c = cfg.colors;
    return text(message, {
      fontSize: "18px",
      margin: "10px 0",
      padding: "10px 14px",
      borderRadius: "12px",
      color: kind === "error" ? "#8A3E0F" : c.ink,
      background: kind === "error" ? "#FBEFE6" : c.green_tint,
    });
  }

  function clear() {
    stopPolling();
    rootEl.innerHTML = "";
  }

  function say(message, kind) {
    const slot = document.getElementById("bhajan-message");
    if (!slot) return;
    slot.innerHTML = "";
    if (message) slot.appendChild(note(message, kind));
  }

  function sayError(status) {
    say(T(errorKey(status)), "error");
  }

  // ---- the room list -------------------------------------------------------------------------

  async function showList() {
    await leaveAudio();
    current = null;
    clear();
    const c = cfg.colors;
    rootEl.appendChild(
      text(T("title"), {
        fontSize: "28px",
        fontWeight: "700",
        color: c.green_ink,
        margin: "8px 0 14px",
      })
    );
    const message = el("div");
    message.id = "bhajan-message";
    rootEl.appendChild(message);

    const listBox = el("div");
    rootEl.appendChild(listBox);

    const result = await api("/rooms");
    if (!result.ok) {
      sayError(result.status);
    } else if (!result.data.items.length) {
      listBox.appendChild(note(T("no_rooms")));
    } else {
      result.data.items.forEach(function (room) {
        const card = el("div", {
          border: "2px solid " + c.warm_border,
          borderRadius: "18px",
          background: c.card_cream,
          padding: "14px 16px",
          margin: "12px 0",
        });
        card.appendChild(text(room.title, { fontSize: "24px", fontWeight: "700", color: c.ink }));
        card.appendChild(
          text(
            room.lead ? T("singing_now") + ": " + room.lead.name : T("nobody_singing"),
            { fontSize: "18px", color: c.muted_ink, margin: "4px 0 6px" }
          )
        );
        card.appendChild(bigButton(T("join"), function () { openRoom(room.id); }, "primary"));
        listBox.appendChild(card);
      });
    }
    rootEl.appendChild(startForm());
    rootEl.appendChild(bigButton(T("back"), function () { window.location.assign("/"); }));
  }

  /** For moderators. Anyone can open it; the server says 403 to everyone else. */
  function startForm() {
    const c = cfg.colors;
    const details = el("details", { margin: "18px 0", fontSize: "18px" });
    const summary = el("summary", { fontSize: "20px", fontWeight: "700", color: c.green_ink, cursor: "pointer" });
    summary.textContent = T("start_title");
    details.appendChild(summary);

    // A field's edge must be seen against the cream: warm_border on cream is 1.2:1, muted_ink is 5.4:1.
    const field = { width: "100%", minHeight: "56px", fontSize: "20px", borderRadius: "12px", border: "2px solid " + c.muted_ink, background: c.card_cream, color: c.ink, boxSizing: "border-box" };
    const title = el("input", Object.assign({}, field, { padding: "0 14px", margin: "10px 0" }));
    title.placeholder = T("room_name");
    title.setAttribute("aria-label", T("room_name"));
    const circle = el("select", Object.assign({}, field, { margin: "6px 0" }));
    circle.setAttribute("aria-label", T("circle"));
    details.appendChild(title);
    details.appendChild(circle);
    details.appendChild(
      bigButton(T("start_button"), async function () {
        if (!title.value.trim() || !circle.value) {
          say(T("fill_both"), "error");
          return;
        }
        const made = await api("/rooms", "POST", { circle_id: circle.value, title: title.value.trim() });
        if (!made.ok) return sayError(made.status);
        openRoom(made.data.id);
      }, "primary")
    );
    api("/circles").then(function (r) {
      if (!r.ok || !Array.isArray(r.data)) return;
      r.data.forEach(function (item) {
        const option = el("option");
        option.value = item.id;
        option.textContent = item.name;
        circle.appendChild(option);
      });
    });
    return details;
  }

  // ---- the room --------------------------------------------------------------------------------

  async function ensureLib() {
    if (root.LivekitClient) return root.LivekitClient;
    await new Promise(function (resolve, reject) {
      const s = document.createElement("script");
      s.src = LK_SRC;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
    return root.LivekitClient;
  }

  async function openRoom(roomId) {
    clear();
    current = { id: roomId, room: null, state: null, micOn: false, shape: "", view: {} };
    buildRoomScreen();
    say(T("connecting"));
    await joinAudio(roomId);
    await refresh();
    pollTimer = setInterval(refresh, cfg.pollMs || POLL_MS);
  }

  function buildRoomScreen() {
    const c = cfg.colors;
    const v = current.view;
    v.title = text("", { fontSize: "26px", fontWeight: "700", color: c.green_ink, margin: "6px 0" });
    v.message = el("div");
    v.message.id = "bhajan-message";
    v.now = text("", { fontSize: "30px", fontWeight: "700", color: c.ink, margin: "14px 0 4px" });
    v.live = text("", { fontSize: "20px", fontWeight: "700", color: c.deep_green, margin: "4px 0" });
    v.live.setAttribute("aria-live", "polite");
    v.now.setAttribute("aria-live", "polite"); // a screen reader says who is singing when it changes
    v.buttons = el("div");
    v.queueTitle = text(T("waiting_list"), { fontSize: "20px", fontWeight: "700", color: c.green_ink, margin: "16px 0 4px" });
    v.queue = el("div");
    v.hereTitle = text(T("here"), { fontSize: "20px", fontWeight: "700", color: c.green_ink, margin: "16px 0 4px" });
    v.here = text("", { fontSize: "18px", color: c.muted_ink });
    v.audio = el("div");
    v.audio.id = "bhajan-audio";
    v.tap = el("div");
    v.manage = el("div");
    v.leave = bigButton(T("leave"), function () { showList(); });
    [v.title, v.message, v.now, v.live, v.tap, v.buttons, v.queueTitle, v.queue, v.manage, v.hereTitle, v.here, v.audio, v.leave].forEach(function (node) {
      rootEl.appendChild(node);
    });
  }

  async function act(path, method, button) {
    if (button) button.disabled = true;
    const result = await api("/rooms/" + current.id + path, method || "POST");
    if (button) button.disabled = false;
    if (!result.ok) {
      sayError(result.status);
      if (result.status === 404 || result.status === 409) await refresh();
      return;
    }
    say("");
    applyState(result.data);
  }

  async function refresh() {
    if (!current) return;
    const result = await api("/rooms/" + current.id);
    if (!result.ok) {
      if (result.status === 404) {
        say(T("err_notfound"), "error");
        stopPolling();
      } else {
        sayError(result.status);
      }
      return;
    }
    applyState(result.data);
  }

  function applyState(state) {
    if (!current) return;
    current.state = state;
    const v = current.view;
    const c = cfg.colors;
    v.title.textContent = state.room.title;
    if (state.room.status === "closed") {
      say(T("err_closed"), "error");
      stopPolling();
      leaveAudio();
      v.buttons.innerHTML = "";
      return;
    }
    v.now.textContent = state.lead ? T("singing_now") + ": " + state.lead.name : T("nobody_singing");
    v.live.textContent = state.you.is_lead ? T("you_are_live") : state.lead ? T("listening") : "";

    const shape = signature([buttonsFor(state.you, state.lead), state.you.position, state.you.can_manage, state.queue.map(function (e) { return e.user_id; })]);
    if (shape !== current.shape) {
      current.shape = shape;
      renderButtons(state);
      renderQueue(state);
    }
    renderHere();
    syncMic(state);
    rejoinIfTokenTooWeak(state);
  }

  /** The server says "you lead" but the token held says "cannot publish": join again with a fresh one. */
  function rejoinIfTokenTooWeak(state) {
    if (!current || !current.room || current.rejoining) return;
    if (!needsFreshToken(state.you, current.tokenCanPublish)) return;
    const minMs = cfg.rejoinMinMs !== undefined ? cfg.rejoinMinMs : REJOIN_MIN_MS;
    if (Date.now() - (current.joinedAt || 0) < minMs) return;
    const id = current.id;
    current.rejoining = true;
    joinAudio(id).then(function () {
      if (current && current.id === id) return syncMic(current.state);
    }).finally(function () {
      if (current) current.rejoining = false;
    });
  }

  function renderButtons(state) {
    const v = current.view;
    v.buttons.innerHTML = "";
    buttonsFor(state.you, state.lead).forEach(function (id) {
      if (id === "pass") v.buttons.appendChild(bigButton(T("pass"), function (b) { act("/next", "POST", b); }, "primary"));
      if (id === "take") v.buttons.appendChild(bigButton(T("take"), function (b) { act("/next", "POST", b); }, "primary"));
      if (id === "handover") v.buttons.appendChild(bigButton(T("handover"), function (b) { act("/next", "POST", b); }));
      if (id === "raise") v.buttons.appendChild(bigButton(T("raise"), function (b) { act("/queue", "POST", b); }, state.you.can_take_floor ? undefined : "primary"));
      if (id === "lower") {
        v.buttons.appendChild(text(T("your_place", { n: state.you.position || "" }), { fontSize: "20px", margin: "6px 0" }));
        v.buttons.appendChild(bigButton(T("lower"), function (b) { act("/queue", "DELETE", b); }));
      }
    });
  }

  function renderQueue(state) {
    const v = current.view;
    v.queue.innerHTML = "";
    v.manage.innerHTML = "";
    v.queueTitle.style.display = state.queue.length ? "block" : "none";
    state.queue.forEach(function (entry) {
      const row = el("div", { display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: "20px", margin: "6px 0" });
      row.appendChild(text(entry.position + ". " + entry.name));
      if (state.you.can_manage) {
        const skip = bigButton(T("skip"), function (b) { act("/queue/" + entry.user_id + "/skip", "POST", b); });
        // Several "Skip" buttons in a row: say whom each one skips, for a screen reader.
        skip.setAttribute("aria-label", T("skip_name", { name: entry.name }));
        skip.style.width = "auto";
        skip.style.minHeight = "48px";
        skip.style.fontSize = "18px";
        row.appendChild(skip);
      }
      v.queue.appendChild(row);
    });
    if (state.you.can_manage) {
      v.manage.appendChild(text(T("moderator_controls"), { fontSize: "20px", fontWeight: "700", color: cfg.colors.green_ink, margin: "20px 0 6px" }));
      v.manage.appendChild(
        bigButton(T("close_room"), function (b) {
          if (window.confirm(T("confirm_close"))) act("/close", "POST", b).then(showList);
        }, "danger")
      );
    }
  }

  // ---- audio -------------------------------------------------------------------------------------

  async function joinAudio(roomId) {
    const v = current.view;
    // A fresh token every time, never one kept from before: the token that can publish lives two
    // minutes, and a hand-over cannot reach a token that was already issued.
    const t = await api("/rooms/" + roomId + "/token", "POST");
    if (!t.ok) {
      current.audioLost = true;
      renderTap(null);
      sayError(t.status);
      return;
    }
    if (current.room) {
      const old = current.room;
      current.room = null; // so the old connection's Disconnected event is not shown as a failure
      try { await old.disconnect(); } catch (e) { /* already gone */ }
    }
    current.micOn = false;
    current.tokenCanPublish = !!t.data.can_publish;
    current.joinedAt = Date.now();
    try {
      const LK = await ensureLib();
      const room = new LK.Room({
        audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        publishDefaults: { dtx: true },
      });
      room.on(LK.RoomEvent.TrackSubscribed, function (track) {
        if (track.kind !== "audio") return;
        const audio = track.attach();
        v.audio.appendChild(audio);
      });
      room.on(LK.RoomEvent.TrackUnsubscribed, function (track) {
        track.detach().forEach(function (node) { node.remove(); });
      });
      room.on(LK.RoomEvent.Disconnected, function () {
        if (current && current.room === room) {
          current.audioLost = true;
          current.micOn = false;
          renderTap(room);
          say(T("err_audio"), "error");
        }
      });
      room.on(LK.RoomEvent.AudioPlaybackStatusChanged, function () { renderTap(room); });
      await room.connect(t.data.url, t.data.token);
      current.room = room;
      current.audioLost = false;
      try { await room.startAudio(); } catch (e) { /* the tap button below covers it */ }
      renderTap(room);
      say("");
    } catch (e) {
      current.audioLost = true;
      renderTap(null);
      say(T("err_audio"), "error");
    }
  }

  /** Browsers keep audio silent until a tap; this button is that tap if the join tap was not enough. */
  function renderTap(room) {
    if (!current) return;
    const v = current.view;
    v.tap.innerHTML = "";
    if (current.audioLost) {
      v.tap.appendChild(bigButton(T("reconnect"), async function (b) {
        b.disabled = true;
        say(T("connecting"));
        await joinAudio(current.id);
        if (current && current.room) await syncMic(current.state);
      }, "primary"));
    } else if (room && !room.canPlaybackAudio) {
      v.tap.appendChild(bigButton(T("tap_to_hear"), async function () {
        try { await room.startAudio(); } catch (e) { /* still blocked */ }
        renderTap(room);
      }, "primary"));
    }
  }

  function renderHere() {
    if (!current || !current.room) return;
    const room = current.room;
    const remote = [];
    room.remoteParticipants.forEach(function (p) { remote.push(p.name || p.identity); });
    const names = hereNames(room.localParticipant.name || T("you"), remote);
    current.view.here.textContent = names.length + ": " + names.join(", ");
  }

  /** The server says who leads; the microphone follows. A failure is shown, never hidden. */
  async function syncMic(state) {
    if (!current || !current.room) return;
    const plan = micPlan(state.you, current.micOn);
    if (plan === "none") return;
    try {
      await current.room.localParticipant.setMicrophoneEnabled(plan === "on");
      current.micOn = plan === "on";
    } catch (e) {
      if (plan === "on") say(T("mic_blocked"), "error");
    }
  }

  async function leaveAudio() {
    if (current && current.room) {
      try { await current.room.disconnect(); } catch (e) { /* already gone */ }
      current.room = null;
    }
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  // ---- start -------------------------------------------------------------------------------------

  /** A clear focus ring for keyboard use: the browser's default is weak on cream. */
  function addFocusStyle() {
    const style = document.createElement("style");
    style.id = "bhajan-focus-style";
    style.textContent =
      "#bhajan-root button:focus-visible, #bhajan-root input:focus-visible, #bhajan-root select:focus-visible, #bhajan-root summary:focus-visible" +
      " { outline: 4px solid " + cfg.colors.deep_green + "; outline-offset: 3px; }";
    document.head.appendChild(style);
  }

  function start(config) {
    cfg = config;
    addFocusStyle();
    lang = chosenLanguage();
    rootEl = document.getElementById("bhajan-root");
    if (!rootEl) return;
    window.addEventListener("beforeunload", function () {
      if (current && current.room) current.room.disconnect();
    });
    showList();
  }

  root.SatBhajan = Object.assign({ start: start }, logic);
})(typeof window !== "undefined" ? window : globalThis);
