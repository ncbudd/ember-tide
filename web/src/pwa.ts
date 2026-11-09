import { api, isApp } from "./api";

/**
 * The web app on a phone: iPhone Safari vs. added to the Home Screen, Web Push, and the service worker.
 * iPhone rules (iOS 16.4+): notifications only work for a site added to the Home Screen and opened from that icon.
 */

export const isIOS = (): boolean =>
  /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
export const isAndroidBrowser = (): boolean => !isApp() && /Android/.test(navigator.userAgent);

/** Opened from the Home Screen (iOS sets navigator.standalone; others use the display-mode media query). */
const isStandalone = (): boolean =>
  (navigator as unknown as { standalone?: boolean }).standalone === true || window.matchMedia?.("(display-mode: standalone)").matches;

/** iOS major.minor from the user agent, or null. */
function iosVersion(): number | null {
  const m = navigator.userAgent.match(/OS (\d+)[_.](\d+)/);
  return isIOS() && m ? Number(m[1]) + Number(m[2]) / 100 : null;
}

export type WebPushSupport =
  | "app"            // the Android app: it has its own native notifications
  | "ok"             // can ask now
  | "install"        // iPhone in Safari: add to the Home Screen first, then open it from there
  | "old-ios"        // iPhone older than 16.4
  | "insecure"       // not https (push needs a secure page)
  | "unsupported";   // this browser has no Web Push

export function webPushSupport(): WebPushSupport {
  if (isApp()) return "app";
  if (!window.isSecureContext) return "insecure";
  if (isIOS()) {
    const v = iosVersion();
    if (v !== null && v < 16.04) return "old-ios";
    if (!isStandalone()) return "install";
  }
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return "unsupported";
  return "ok";
}

export const notifPermission = (): NotificationPermission | "unsupported" =>
  "Notification" in window ? Notification.permission : "unsupported";

/** Register the service worker (push only; it doesn't cache pages). Browsers only, never inside the Android app. */
export function registerServiceWorker(): void {
  if (isApp() || !("serviceWorker" in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => { /* push just won't be offered */ });
}

function keyBytes(b64u: string): Uint8Array {
  const s = atob(b64u.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64u.length % 4)) % 4));
  return Uint8Array.from(s, c => c.charCodeAt(0));
}
const b64u = (buf: ArrayBuffer | null): string =>
  buf ? btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") : "";

export interface WebPushInfo { available: boolean; key: string | null; devices: { id: string; device: string; ts: number; platform: string | null }[] }

export const webPushInfo = (person: string) => api.get<WebPushInfo>(`/api/notify/me/webpush?person=${encodeURIComponent(person)}`);

/** Ask for permission (must run from a tap), subscribe this browser and register it for `person`. */
export async function enableWebPush(person: string): Promise<"on" | "denied"> {
  const info = await webPushInfo(person);
  if (!info.available || !info.key) throw new Error("Web notifications aren't available on this server.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return "denied";
  registerServiceWorker();
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  const want = keyBytes(info.key);
  const same = sub?.options.applicationServerKey && b64u(sub.options.applicationServerKey) === info.key;
  if (sub && !same) { await sub.unsubscribe(); sub = null; }            // the server's key changed: subscribe afresh
  sub = sub ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: want as BufferSource });
  await api.post("/api/notify/me/webpush", {
    endpoint: sub.endpoint, p256dh: b64u(sub.getKey("p256dh")), auth: b64u(sub.getKey("auth")), person,
    platform: isIOS() ? "ios" : isAndroidBrowser() ? "android" : "desktop",
  });
  return "on";
}

/** This browser's current subscription endpoint, if any. */
export async function currentEndpoint(): Promise<string | null> {
  if (!("serviceWorker" in navigator)) return null;
  const reg = await navigator.serviceWorker.getRegistration("/");
  return (await reg?.pushManager.getSubscription())?.endpoint ?? null;
}

export async function disableWebPush(person: string): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await api.post("/api/notify/me/push/remove", { endpoint: sub.endpoint, person }).catch(() => {});
    await sub.unsubscribe();
  }
}
