// The satsang listener's player: one global, `window.satsang` = { play, stop, status }.
// Plain JavaScript on purpose, so tests/test_satsang_player.py can run this very file under Node.
//
//   play()   Called by the big button's own DOM click handler, NOT through the Reflex server: iOS
//            Safari only starts audio that is requested during a touch, and a round trip loses it.
//            Reads the stream address from #satsang-audio's data-url, starts hls.js (or the browser's
//            own HLS), and asks the element to play, all before it returns.
//   stop()   Tears it all down and goes idle. A late event from a torn-down player changes nothing.
//   status() { status, waiting_s, error_kind } and nothing else (no address, no counters). The page
//            polls it; the words the elder reads are chosen in Python, from these three fields.
//
// status:  idle | connecting | playing | stalled | failed
// error_kind (only when failed):
//   unreachable  the stream never loaded before any sound. On a phone this is what an untrusted
//                certificate (or no route) looks like, so the screen says so.
//   media / other  the player could not decode / hit something it cannot name
//   unsupported  this browser can play neither hls.js nor native HLS
//   no_stream    no audio element or no address (a page bug, reported rather than thrown)
//   blocked      the browser refused to start audio from the tap
//
// A drop AFTER sound is never "failed": the speaker may come back, so it keeps trying (five quick
// retries, then one every five seconds), and the page decides when to say "this may have ended".
(function (root) {
  "use strict";

  var NETWORK_QUICK_RETRIES = 5;
  var MEDIA_RETRIES = 3;
  var NETWORK_SLOW_RETRY_MS = 5000;
  var NATIVE_RETRY_MS = 3000;
  var HLS_TYPE = "application/vnd.apple.mpegurl";

  var generation = 0; // bumped by every play() and stop(): an event from an older one is ignored
  var state = fresh("idle");

  function now() {
    return root.Date.now();
  }

  function fresh(status) {
    return {
      status: status,
      since: now(),
      stalledSince: null,
      errorKind: "",
      everPlayed: false,
      netRetries: 0,
      mediaRetries: 0,
      retryPending: false,
      hls: null,
      audio: null,
      listeners: [],
    };
  }

  function seconds(from) {
    return Math.max(0, Math.floor((now() - from) / 1000));
  }

  function status() {
    var waiting = 0;
    if (state.status === "connecting") waiting = seconds(state.since);
    else if (state.status === "stalled" && state.stalledSince !== null) waiting = seconds(state.stalledSince);
    return {
      status: state.status,
      waiting_s: waiting,
      error_kind: state.status === "failed" ? state.errorKind : "",
    };
  }

  function listen(audio, type, fn) {
    audio.addEventListener(type, fn);
    state.listeners.push([type, fn]);
  }

  function teardown() {
    var audio = state.audio;
    if (audio) {
      state.listeners.forEach(function (pair) {
        audio.removeEventListener(pair[0], pair[1]);
      });
    }
    state.listeners = [];
    if (state.hls) {
      state.hls.destroy();
      state.hls = null;
    }
  }

  function fail(kind) {
    state.status = "failed";
    state.errorKind = kind;
    teardown();
  }

  function markStalled() {
    if (!state.everPlayed) return; // before any sound it is still "connecting"
    if (state.stalledSince === null) state.stalledSince = now();
    state.status = "stalled";
  }

  function onPlaying() {
    state.everPlayed = true;
    state.stalledSince = null;
    state.netRetries = 0;
    state.mediaRetries = 0;
    state.status = "playing";
  }

  function scheduleRetry(gen, ms, action) {
    if (state.retryPending) return;
    state.retryPending = true;
    root.setTimeout(function () {
      if (gen !== generation) return;
      state.retryPending = false;
      action();
    }, ms);
  }

  function onHlsError(gen, data) {
    if (gen !== generation || !data || !data.fatal) return; // non-fatal: hls.js absorbs it itself
    var types = root.Hls.ErrorTypes;
    var details = root.Hls.ErrorDetails;
    if (data.type === types.NETWORK_ERROR) {
      var neverLoaded =
        !state.everPlayed &&
        (data.details === details.MANIFEST_LOAD_ERROR || data.details === details.MANIFEST_LOAD_TIMEOUT);
      if (neverLoaded) return fail("unreachable");
      markStalled();
      if (state.netRetries < NETWORK_QUICK_RETRIES) {
        state.netRetries += 1;
        state.hls.startLoad();
      } else {
        scheduleRetry(gen, NETWORK_SLOW_RETRY_MS, function () {
          if (state.hls) state.hls.startLoad();
        });
      }
    } else if (data.type === types.MEDIA_ERROR) {
      if (state.mediaRetries < MEDIA_RETRIES) {
        state.mediaRetries += 1;
        state.hls.recoverMediaError();
      } else {
        fail("media");
      }
    } else {
      fail("other");
    }
  }

  function play() {
    stop();
    var gen = ++generation;
    var audio = root.document.getElementById("satsang-audio");
    var url = audio && audio.dataset && audio.dataset.url;
    state = fresh("connecting");
    if (!audio || !url) return fail("no_stream");
    state.audio = audio;

    listen(audio, "playing", function () {
      if (gen === generation) onPlaying();
    });
    var stalled = function () {
      if (gen === generation && (state.status === "playing" || state.status === "stalled")) markStalled();
    };
    listen(audio, "waiting", stalled);
    listen(audio, "stalled", stalled);

    if (root.Hls && root.Hls.isSupported()) {
      var hls = new root.Hls({
        lowLatencyMode: false,
        manifestLoadingMaxRetry: 2,
        manifestLoadingRetryDelay: 1000,
        levelLoadingMaxRetry: 4,
        fragLoadingMaxRetry: 4,
      });
      state.hls = hls;
      hls.on(root.Hls.Events.ERROR, function (event, data) {
        onHlsError(gen, data);
      });
      hls.loadSource(url);
      hls.attachMedia(audio);
    } else if (audio.canPlayType && audio.canPlayType(HLS_TYPE)) {
      // The browser's own HLS (Safari). Its media stack, not the page, fetches the stream.
      listen(audio, "error", function () {
        if (gen !== generation) return;
        if (!state.everPlayed) return fail("unreachable");
        markStalled();
        scheduleRetry(gen, NATIVE_RETRY_MS, function () {
          audio.load();
          audio.play();
        });
      });
      audio.src = url;
    } else {
      return fail("unsupported");
    }

    var started = audio.play();
    if (started && started.catch) {
      started.catch(function (err) {
        if (gen === generation && err && err.name === "NotAllowedError") fail("blocked");
      });
    }
  }

  function stop() {
    var audio = state.audio;
    var active = audio || state.hls;
    generation += 1;
    teardown();
    if (audio && active) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    state = fresh("idle");
  }

  root.satsang = { play: play, stop: stop, status: status }; // window.satsang
})(typeof window !== "undefined" ? window : this);
