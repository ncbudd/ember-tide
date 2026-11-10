import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "./types";

/** Running inside the Android app (Capacitor) rather than a browser tab. */
export const isApp = (): boolean => !!(window as unknown as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor?.isNativePlatform?.();

/** In the app the UI is bundled, so requests go to the server the person chose, with their session as a bearer token.
 *  In a browser everything is same-origin with the cookie, as before. */
export const appServer = (): string => (isApp() ? (localStorage.getItem("et-server") ?? "") : "");
export const appToken = (): string => (isApp() ? (localStorage.getItem("et-token") ?? "") : "");
export function setAppSession(server: string | null, token: string | null) {
  if (server !== null) localStorage.setItem("et-server", server.replace(/\/+$/, ""));
  if (token === null) localStorage.removeItem("et-token"); else localStorage.setItem("et-token", token);
  window.dispatchEvent(new Event("et-app-session"));
}

/** fetch that gives up after `ms`, so a Wi-Fi that isn't home can't leave the app waiting. Never sends credentials. */
function fetchQuick(url: string, ms = 4000): Promise<Response> {
  const ctl = new AbortController();
  const timer = window.setTimeout(() => ctl.abort(), ms);
  return fetch(url, { credentials: "omit", signal: ctl.signal }).finally(() => window.clearTimeout(timer));
}

/** The server's id (from /healthz) as it was when the person signed in or moved to the https address: the home
 *  address is only used as a fallback when it answers with this same id. */
const SERVER_ID = "et-server-id";
const DECLINED = "et-secure-declined";        // the https address the person said no to, so it isn't asked every launch
const store = {
  get: (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  del: (k: string) => { try { localStorage.removeItem(k); } catch { /* private mode */ } },
};
export function rememberServerId(id: unknown) { if (typeof id === "string" && id) store.set(SERVER_ID, id); }

/** Picking a different server: forget everything tied to the old one. */
export function forgetAppServer() {
  setAppSession("", null);
  for (const k of ["et-server", "et-lan", SERVER_ID, DECLINED]) store.del(k);
  forgetLan();
}

/** Already on the https address but signed in before the server id was kept: learn it from the https address (TLS
 *  proves who answered), so the home-address fallback can work. */
export async function learnServerId(): Promise<void> {
  if (!isApp() || store.get(SERVER_ID) || !appServer().startsWith("https://")) return;
  try { const j = await (await fetchQuick(appServer() + "/healthz")).json(); if (j?.ok === true) rememberServerId(j.id); } catch { /* next launch */ }
}

/** Reached over plain http at home: does the server offer an encrypted (https) address that answers as this same
 *  server? Returns that address for the person to confirm (never moves on its own: anyone on the home Wi-Fi could
 *  fake the plain-http answer), or null. An address the person already said no to isn't offered again. */
export async function secureAddressOffer(): Promise<string | null> {
  if (!isApp()) return null;
  const cur = appServer();
  if (!cur.startsWith("http://")) return null;
  try {
    const a = await (await fetchQuick(cur + "/healthz")).json();
    const sec = typeof a?.secure_url === "string" && a.secure_url.startsWith("https://") ? a.secure_url.replace(/\/+$/, "") : "";
    if (!sec || typeof a.id !== "string" || store.get(DECLINED) === sec) return null;
    const b = await (await fetchQuick(sec + "/healthz")).json();
    if (b?.ok !== true || b.id !== a.id) return null;
    return sec;
  } catch { return null; }
}

/** The person said yes: move to the https address, keeping the home one (and the server's id, from the https
 *  answer) for when the internet is down. */
export async function switchToSecureAddress(sec: string): Promise<boolean> {
  const cur = appServer();
  try {
    const b = await (await fetchQuick(sec + "/healthz")).json();     // checked again over TLS: this id is the real one
    if (b?.ok !== true || typeof b.id !== "string") return false;
    rememberServerId(b.id);
  } catch { return false; }
  if (cur.startsWith("http://")) store.set("et-lan", cur);
  store.del(DECLINED);
  forgetLan();
  setAppSession(sec, appToken() || null);
  return true;
}
export function declineSecureAddress(sec: string) { store.set(DECLINED, sec); }

// ---- the home address as a fallback ----
// Only after the home address has answered /healthz (no credentials) with the saved server id; that answer is kept
// for a minute and checked again after a network change or a failure, so a session never goes in the clear to
// whatever happens to sit at that address on another Wi-Fi.
const LAN_TTL_MS = 60_000;
let lanVerdict: { lan: string; ok: boolean; at: number } | null = null;
let lanCheck: Promise<boolean> | null = null;
let lanGen = 0;
function forgetLan() { lanVerdict = null; lanCheck = null; lanGen++; }
if (typeof window !== "undefined") {
  window.addEventListener("online", forgetLan);
  window.addEventListener("offline", forgetLan);
  (navigator as unknown as { connection?: EventTarget }).connection?.addEventListener?.("change", forgetLan);
}
function lanIsOurs(lan: string): Promise<boolean> {
  const id = store.get(SERVER_ID);
  if (!id) return Promise.resolve(false);                            // nothing to check it against: never fall back
  if (lanVerdict && lanVerdict.lan === lan && Date.now() - lanVerdict.at < LAN_TTL_MS) return Promise.resolve(lanVerdict.ok);
  if (lanCheck) return lanCheck;
  const gen = lanGen;
  const check = (async () => {
    let ok = false;
    try { const j = await (await fetchQuick(lan + "/healthz", 2500)).json(); ok = j?.ok === true && j.id === id; } catch { ok = false; }
    if (gen === lanGen) { lanVerdict = { lan, ok, at: Date.now() }; lanCheck = null; }    // a network change meanwhile: don't keep it
    return ok;
  })();
  lanCheck = check;
  return check;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Embertide": "1" };     // proves the request came from this app, not another site
  if (body) headers["content-type"] = "application/json";
  if (isApp()) {
    headers["X-Embertide-Client"] = "app";
    if (appToken()) headers["Authorization"] = `Bearer ${appToken()}`;
  }
  // never leave a tap hanging: after 12 s it fails with a clear message instead of doing nothing
  const ctl = new AbortController();
  const timer = window.setTimeout(() => ctl.abort(), 12_000);
  let r: Response;
  try {
    const init: RequestInit = { method, headers, body: body ? JSON.stringify(body) : undefined, credentials: isApp() ? "omit" : "same-origin", signal: ctl.signal };
    try {
      r = await fetch(appServer() + path, init);
    } catch (e) {
      // the encrypted address is out of reach (internet down): the home address still works on the home Wi-Fi, once
      // it has proved it's this same server (see lanIsOurs); otherwise the session never leaves for it
      const lan = isApp() && appServer().startsWith("https://") ? store.get("et-lan") : null;
      if (!lan || ctl.signal.aborted || !(await lanIsOurs(lan)) || ctl.signal.aborted) throw e;
      try { r = await fetch(lan + path, init); } catch (e2) { forgetLan(); throw e2; }
    }
  } catch {
    throw new Error("can't reach your server right now");
  } finally {
    window.clearTimeout(timer);
  }
  if (r.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("et-signed-out"));
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const d = (await r.json()).detail;
      if (typeof d === "string") msg = d;
      else if (Array.isArray(d) && d.length) {
        const first = d[0] as { loc?: (string | number)[]; msg?: string };
        const field = (first.loc ?? []).filter(x => typeof x === "string" && x !== "body").pop();
        msg = `${field ? String(field).replace(/_/g, " ") + ": " : ""}${first.msg ?? "not allowed"}`;
      }
    } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}
export const api = {
  get: <T,>(p: string) => req<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => req<T>("POST", p, b ?? {}),
  put: <T,>(p: string, b: unknown) => req<T>("PUT", p, b),
  patch: <T,>(p: string, b: unknown) => req<T>("PATCH", p, b),
  del: <T,>(p: string) => req<T>("DELETE", p),
};

/** Live snapshot over WebSocket, falling back to polling. `apply` lets actions show their result instantly. */
export function useLive() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  useEffect(() => {
    let stop = false, retry: number | undefined, poll: number | undefined, lastMsg = Date.now();
    let gen = 0, fails = 0;                                // gen: which connect attempt is current (a pause moves it on)
    const again = () => {
      // poll meanwhile, and try again a little later each time it fails (3 s, 6 s, 12 s … up to 30 s)
      if (!poll) poll = window.setInterval(() => api.get<Snapshot>("/api/snapshot").then(setSnap).catch(() => {}), 4000);
      fails++;
      clearTimeout(retry);
      retry = window.setTimeout(connect, Math.min(30_000, 3000 * 2 ** Math.min(fails - 1, 4)));
    };
    const open = (url: string) => {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      let closed = false;                                  // this connection only: a late close from an old one does nothing
      ws.onopen = () => { if (closed) return; fails = 0; setConnected(true); if (poll) { clearInterval(poll); poll = undefined; } };
      ws.onmessage = (e) => { if (closed) return; lastMsg = Date.now(); try { setSnap(JSON.parse(e.data)); } catch { /* ignore */ } };
      ws.onclose = (ev) => {
        if (closed) return;
        closed = true;
        setConnected(false);
        if (stop) return;
        if (ev.code === 4401) { window.dispatchEvent(new Event("et-signed-out")); return; }
        again();                                           // 4400 (ticket used up or late) lands here too: a new ticket
      };
      detach = () => { closed = true; ws.onopen = ws.onmessage = ws.onclose = null; };
    };
    const connect = () => {
      if (stop) return;
      const base = appServer();
      if (!base) {                                         // browser / PWA: same origin, the cookie signs the socket in
        open(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`);
        return;
      }
      // the app: a fresh single-use ticket for every connection, so the session itself never goes in a URL
      const mine = ++gen;
      api.post<{ ticket: string }>("/api/live/ticket")
        .then(r => { if (!stop && mine === gen) open(`${base.replace(/^http/, "ws")}/api/live?ticket=${encodeURIComponent(r.ticket)}`); })
        .catch(() => { if (!stop && mine === gen) again(); });   // a signed-out session is reported by api itself (401)
    };
    let detach = () => {};
    // battery: no live connection while the app is in the background. Close it 30 s after the app is hidden and
    // reconnect (with a fresh snapshot) as soon as it is shown again.
    let hideTimer: number | undefined;
    const pause = () => { stop = true; gen++; fails = 0; clearTimeout(retry); clearInterval(poll); poll = undefined; detach(); wsRef.current?.close(); wsRef.current = null; setConnected(false); };
    const onVis = () => {
      if (document.visibilityState === "hidden") { clearTimeout(hideTimer); hideTimer = window.setTimeout(pause, 30_000); return; }
      clearTimeout(hideTimer);
      // always refresh on coming back: an iPhone freezes timers in the background, so the 30 s pause may never have
      // run and the socket may be dead without knowing it yet
      api.get<Snapshot>("/api/snapshot").then(setSnap).catch(() => {});
      if (stop) { stop = false; connect(); return; }
      const shownAt = Date.now();
      window.setTimeout(() => {
        if (!stop && lastMsg < shownAt && wsRef.current) { wsRef.current.close(); }     // silent socket: onclose reconnects
      }, 10_000);
    };
    document.addEventListener("visibilitychange", onVis);
    api.get<Snapshot>("/api/snapshot").then(setSnap).catch(() => {});
    connect();
    return () => { document.removeEventListener("visibilitychange", onVis); clearTimeout(hideTimer); pause(); };
  }, []);
  return { snap, connected, apply: (s: Snapshot) => setSnap(s) };
}
