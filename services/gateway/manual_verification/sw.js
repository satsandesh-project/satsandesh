// Throwaway manual-verification service worker — see README.md in this
// directory. The only job here: turn a Push API `push` event into a real
// OS-level notification, so a human can see the round-trip actually work.

self.addEventListener("install", () => {
  // Activate immediately rather than waiting for every existing tab of
  // this page to close first — this is a one-off verification page, not
  // a real app with a careful update strategy to protect.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let payload = { title: "SatSandesh (manual verification)", body: "" };
  if (event.data) {
    try {
      const data = event.data.json();
      payload.title = data.title || payload.title;
      payload.body = data.body || "";
    } catch (err) {
      // app/push.py always sends JSON, but if that ever changes, fall
      // back to raw text rather than silently showing nothing.
      payload.body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      tag: "satsandesh-manual-verification",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
});
