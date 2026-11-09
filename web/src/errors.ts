/**
 * Reports app errors to the server (Studio → Help → Troubleshooting shows them), so a screen that breaks on someone's
 * phone isn't just a mystery. Small and polite: the same message is sent once a minute at most, ten per session,
 * and nothing personal goes along (no query strings, no names or values from the screen).
 */
import { appServer, appToken, isApp } from "./api";

let sent = 0;
const lastSent = new Map<string, number>();

/** The web bundle's build id (its hashed file name), so reports tell which version broke. */
function buildId(): string {
  const s = Array.from(document.scripts).map(x => x.src).find(src => /\/assets\/index-[^/]+\.js/.test(src));
  return s ? s.replace(/^.*\/assets\/index-/, "").replace(/\.js.*$/, "").slice(0, 20) : "dev";
}

export function reportError(kind: "render" | "error" | "rejection", message: string, stack = ""): void {
  try {
    const key = `${kind}:${message}`.slice(0, 200);
    const now = Date.now();
    if (sent >= 10 || now - (lastSent.get(key) ?? 0) < 60_000) return;
    lastSent.set(key, now);
    sent++;
    const body = {
      kind, message: String(message).slice(0, 500), stack: String(stack).slice(0, 4000),
      route: location.pathname.slice(0, 200), version: buildId(),
      screen: `${window.innerWidth}x${window.innerHeight}@${Math.round((window.devicePixelRatio || 1) * 10) / 10}`,
      agent: navigator.userAgent.slice(0, 300), app: isApp(),
    };
    const headers: Record<string, string> = { "content-type": "application/json", "X-Embertide": "1" };
    if (isApp()) {
      headers["X-Embertide-Client"] = "app";
      const t = appToken();
      if (t) headers.Authorization = `Bearer ${t}`;
    }
    void fetch(appServer() + "/api/client-error", { method: "POST", headers, body: JSON.stringify(body), keepalive: true,
      credentials: isApp() ? "omit" : "same-origin" }).catch(() => { /* offline: nothing to do */ });
  } catch { /* reporting must never cause another error */ }
}

/** Uncaught errors and rejected promises anywhere in the page. */
export function installGlobalErrorReporting(): void {
  window.addEventListener("error", e => {
    const err = (e as ErrorEvent).error as Error | undefined;
    const msg = (e as ErrorEvent).message || err?.message || "";
    if (!msg || /ResizeObserver loop/.test(msg)) return;          // a harmless browser notice, not a crash
    reportError("error", msg, err?.stack ?? `${(e as ErrorEvent).filename ?? ""}:${(e as ErrorEvent).lineno ?? ""}`);
  });
  window.addEventListener("unhandledrejection", e => {
    const r = (e as PromiseRejectionEvent).reason as { message?: string; stack?: string } | string | undefined;
    const msg = typeof r === "string" ? r : r?.message ?? "";
    if (!msg || /can't reach your server|abort/i.test(msg)) return;   // network trouble isn't a bug
    reportError("rejection", msg, typeof r === "object" ? r?.stack ?? "" : "");
  });
}
