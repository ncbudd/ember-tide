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
    r = await fetch(appServer() + path, { method, headers, body: body ? JSON.stringify(body) : undefined, credentials: isApp() ? "omit" : "same-origin", signal: ctl.signal });
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
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const base = appServer();
      const ws = new WebSocket(base ? `${base.replace(/^http/, "ws")}/api/live?token=${encodeURIComponent(appToken())}` : `${proto}://${location.host}/api/live`);
      wsRef.current = ws;
      ws.onopen = () => { setConnected(true); if (poll) { clearInterval(poll); poll = undefined; } };
      ws.onmessage = (e) => { lastMsg = Date.now(); try { setSnap(JSON.parse(e.data)); } catch { /* ignore */ } };
      ws.onclose = (ev) => {
        setConnected(false);
        if (stop) return;
        if (ev.code === 4401) { window.dispatchEvent(new Event("et-signed-out")); return; }
        if (!poll) poll = window.setInterval(() => api.get<Snapshot>("/api/snapshot").then(setSnap).catch(() => {}), 4000);
        retry = window.setTimeout(connect, 3000);
      };
    };
    // battery: no live connection while the app is in the background. Close it 30 s after the app is hidden and
    // reconnect (with a fresh snapshot) as soon as it is shown again.
    let hideTimer: number | undefined;
    const pause = () => { stop = true; clearTimeout(retry); clearInterval(poll); poll = undefined; wsRef.current?.close(); wsRef.current = null; };
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
