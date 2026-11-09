/* Ember & Tide service worker: Web Push only (no page caching, so the app is always the server's current one).
   The server encrypts each message for this browser; the browser decrypts it before this handler sees it. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: "Ember & Tide", body: e.data ? e.data.text() : "" }; }
  const actions = Array.isArray(d.actions) ? d.actions.slice(0, 2) : [];
  e.waitUntil(self.registration.showNotification(d.title || "Ember & Tide", {
    body: d.body || "",
    tag: d.tag || undefined,
    renotify: !!d.tag,
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    requireInteraction: !!d.urgent,
    timestamp: d.ts ? d.ts * 1000 : Date.now(),
    actions: actions.map(a => ({ action: a.action, title: a.title })),     // iPhone shows none; tapping opens the app
    data: { url: d.url || "/", actions },
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const data = e.notification.data || {};
  const act = (data.actions || []).find(a => a.action === e.action);
  const url = new URL(act ? act.url : data.url || "/", self.location.origin);
  if (url.origin !== self.location.origin) url.href = self.location.origin + "/";      // only ever open this app
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === url.origin && "focus" in w) {
        if (act || w.url !== url.href) { try { await w.navigate(url.href); } catch { /* cross-scope */ } }
        return w.focus();
      }
    }
    return self.clients.openWindow(url.href);
  })());
});
