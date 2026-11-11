import { registerPlugin } from "@capacitor/core";
import { App as CapApp } from "@capacitor/app";
import { handleBack } from "./components/back";
import { api, appServer, appToken, isApp } from "./api";
import type { Snapshot } from "./types";

interface EmberNative {
  configure(o: { server: string; token: string; person: string }): Promise<void>;
  signOut(): Promise<void>;
  showFlash(o: { kind: string; end: number; name: string; now?: number }): Promise<void>;
  clearFlash(): Promise<void>;
  refreshWidgets(): Promise<void>;
  requestNotifications(): Promise<{ granted: boolean }>;
  setBars(o: { color: string; light: boolean }): Promise<void>;
  share(o: { title: string; text: string }): Promise<void>;
}

const Native = registerPlugin<EmberNative>("EmberNative");

/** Hardware keyboard + launcher actions (Minimal Phone 2), from MainActivity. Older app builds reject. */
export const nativeKeyboard = () => (Native as unknown as { keyboard(): Promise<{ hardware: boolean }> }).keyboard();
export const nativeTakeAction = () => (Native as unknown as { takeAction(): Promise<{ action: string | null }> }).takeAction();
/** The page a tapped notification wants opened (once). Older app builds reject. */
export const nativeTakeLink = () => (Native as unknown as { takeLink(): Promise<{ link: string | null }> }).takeLink();
let lastFlashEnd = -1;
let lastWidget = 0;
let configuredFor = "";

/** Hand the tile, widget and flash notification what they need (only inside the Android app). */
export async function nativeSignedIn(person: string | null) {
  if (!isApp() || !person) return;
  const key = `${appServer()}|${person}|${appToken().slice(0, 8)}`;
  if (key === configuredFor) return;
  configuredFor = key;
  try {
    await Native.configure({ server: appServer(), token: appToken(), person });
    void healthAutoSync();
    void (async () => {
      try {
        const st = await Push.sync();          // set up earlier: make sure the server has this phone for this account
        if (!st.method && st.fcm_available) {
          const mine = await api.get<{ fcm: boolean }>("/api/notify/me/push");
          if (mine.fcm) await Push.enable({ method: "fcm" });   // Google push ready on both ends: just turn it on
        }
      } catch { /* no push on this server or build */ }
    })();
  } catch { /* running in a browser build of the app shell */ }
  askNotificationsOnce();
}

let asking = false;
/** Ask for notification permission once per install, whichever app (full or Simple) the person lands in. Waits until
 *  the screen is showing (a request made while the app is still starting can be dropped), and only counts as asked
 *  once Android has actually answered. */
function askNotificationsOnce() {
  if (!isApp() || asking) return;
  try { if (localStorage.getItem("et-asked-notify")) return; } catch { return; }
  asking = true;
  const go = () => {
    if (document.visibilityState !== "visible") { document.addEventListener("visibilitychange", go, { once: true }); return; }
    // the first-time welcome is on screen: it asks at its last step instead
    if ((window as unknown as { __etWelcome?: boolean }).__etWelcome) { asking = false; return; }
    Native.requestNotifications()
      .then(() => { try { localStorage.setItem("et-asked-notify", "1"); } catch { /* private mode */ } })
      .catch(() => { /* not answered: try again next time */ })
      .finally(() => { asking = false; });
  };
  window.setTimeout(go, 1200);
}

export async function nativeSignedOut() {
  if (!isApp()) return;
  configuredFor = "";
  try { await Native.signOut(); } catch { /* ignore */ }
}

/** Keep the ongoing flash notification and the widget in step with the live state. */
export function nativeSnapshot(snap: Snapshot, person: string | null) {
  if (!isApp() || !person) return;
  const me = snap.people.find(p => p.id === person);
  if (!me) return;
  const f = me.night.flash;
  const end = f && snap.now < f.end ? f.end : 0;
  if (end !== lastFlashEnd) {
    lastFlashEnd = end;
    (end ? Native.showFlash({ kind: f!.kind, end: Math.round(end * 1000), name: me.name, now: Math.round(snap.now * 1000) }) : Native.clearFlash()).catch(() => {});
  }
  if (Date.now() - lastWidget > 60_000) { lastWidget = Date.now(); Native.refreshWidgets().catch(() => {}); }
}

let lastBars = "";
let barsArgs: { color: string; light: boolean } | null = null;
if (typeof window !== "undefined") {
  // Android can repaint the bars its own way after rotating, folding or coming back to the app: put ours back
  const again = () => { if (!barsArgs || !isApp()) return; lastBars = ""; window.setTimeout(() => barsArgs && nativeBars(barsArgs.color, barsArgs.light), 250); };
  window.addEventListener("orientationchange", again);
  window.addEventListener("et-fold", again);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") again(); });
  let rt = 0;
  window.addEventListener("resize", () => { window.clearTimeout(rt); rt = window.setTimeout(again, 400); });
}
/** System bars follow the screen: cream with dark icons in light mode, the page colour with light icons otherwise. */
export function nativeBars(color: string, light: boolean) {
  barsArgs = { color, light };
  if (!isApp()) return;
  const key = color + light;
  if (key === lastBars) return;
  lastBars = key;
  Native.setBars({ color, light }).catch(() => {});
}

/* ---------- Health Connect (Android app only) ---------- */
export interface HealthReport {
  ts: number; from: number; read: Record<string, number>; stored: Record<string, number>;
  warnings: string[]; notes: string[]; nights_rechecked: number;
  // newer app versions: what was already on the server, copies skipped, and what was read per kind and app
  query_from?: number; already?: Record<string, number>; copies_skipped?: number;
  detail?: { kind: string; from: string; records: number; samples: number; first?: number; last?: number }[];
}
export interface HealthStatus {
  sdk: "available" | "update" | "unavailable"; last: number; report?: HealthReport;
  connected?: boolean; granted: string[]; missing: string[]; skin_supported?: boolean; background?: boolean;
  types: string[];             // kinds of data the person chose to share
  available: string[];         // kinds this phone's Health Connect offers
  allowed: string[];           // kinds Health Connect has given permission for
  needs_permission: string[];  // chosen but not yet allowed
}
interface EmberHealth {
  status(): Promise<HealthStatus>;
  connect(): Promise<HealthStatus>;
  sync(o: { full: boolean }): Promise<HealthReport>;
  setTypes(o: { types: string[] }): Promise<HealthStatus>;
  openSettings(): Promise<void>;
  disconnect(): Promise<HealthStatus>;
}
const Health = registerPlugin<EmberHealth>("EmberHealth");
const fill = (s: HealthStatus): HealthStatus => ({
  ...s, granted: s.granted ?? [], missing: s.missing ?? [], types: s.types ?? [], available: s.available ?? [],
  allowed: s.allowed ?? [], needs_permission: s.needs_permission ?? [],
});
export const healthStatus = async () => { if (!isApp()) throw new Error("app only"); return fill(await Health.status()); };
export const healthConnect = async () => fill(await Health.connect());
export const healthSync = (full = false) => Health.sync({ full });
export const healthOpen = () => Health.openSettings();
export const healthDisconnect = async () => fill(await Health.disconnect());
export const healthSetTypes = async (types: string[]) => fill(await Health.setTypes({ types }));

/** When the app opens: if Health Connect is connected and the last sync is over 2 hours old, sync quietly. */
async function healthAutoSync() {
  if (!isApp()) return;
  try {
    const s = await Health.status();
    if (s.sdk === "available" && s.connected && Date.now() - (s.last || 0) > 2 * 3600_000) await Health.sync({ full: false });
  } catch { /* not connected, offline, or no permission yet */ }
}

if (isApp()) {
  CapApp.addListener("resume", () => { void healthAutoSync(); }).catch(() => {});
  CapApp.addListener("backButton", () => { if (!handleBack()) void CapApp.minimizeApp(); }).catch(() => {});
}

/** Links people open elsewhere (invites, reset links) must point at the server, not the app's own internal address. */
export const publicOrigin = (): string => (isApp() ? appServer() : location.origin);

/** The phone's share sheet: native inside the app (the WebView has no Web Share), the browser's elsewhere. */
export const canShare = (): boolean => isApp() || "share" in navigator;
export async function shareText(title: string, text: string): Promise<void> {
  if (isApp()) { await Native.share({ title, text }); return; }
  if ("share" in navigator) { try { await navigator.share({ title, text }); } catch { /* cancelled */ } }
}

/* ---------- app notifications (UnifiedPush through ntfy) ---------- */
export interface PushStatus {
  fcm_available: boolean; allowed: boolean; method: "fcm" | "up" | null;
  distributors: string[]; distributor: string | null; endpoint: boolean; registered: boolean; error: string | null;
}
interface EmberPushApi {
  status(): Promise<PushStatus>; enable(o?: { method: "auto" | "fcm" | "up" }): Promise<PushStatus>; sync(): Promise<PushStatus>; disable(): Promise<PushStatus>; test(): Promise<PushStatus>;
  clear(o: { kind: string }): Promise<void>;
}
const Push = registerPlugin<EmberPushApi>("EmberPush");
export const pushStatus = () => Push.status();
export const pushEnable = (method: "auto" | "fcm" | "up" = "auto") => Push.enable({ method });
export const askNotifications = () => Native.requestNotifications();
export const pushDisable = () => Push.disable();
export const pushTest = () => Push.test();
/** Take one kind of notification (e.g. "pet_question") off this phone: it was answered here or elsewhere. */
export async function pushClear(kind: string) { if (isApp()) { try { await Push.clear({ kind }); } catch { /* older app */ } } }
/** Before signing out: take this phone off the server while the session still works. */
export async function pushBeforeSignOut() { if (isApp()) { try { await Push.disable(); } catch { /* ignore */ } } }

/* ---------- From this phone (Android app only) ---------- */
export type PhoneSource = "location" | "wifi" | "calendar" | "alarm" | "charging" | "watch" | "dial" | "watch_hr";
export interface PhoneStatus {
  sources: Record<PhoneSource, { on: boolean; state: "ok" | "needs_permission" | "needs_background" | "unavailable" | "needs_watch_permission" }>;
  device: string; home_set: boolean; radius_m: number; home_ssid: string | null;
  last_sent: number; last_error: string | null; signed_in: boolean;
  /** live watch heart rate: the watch's permission ("granted" / "needed" / "needs_background" / "unknown"), last relay */
  watch_hr?: { perm: string; last_sent: number; last_count: number; wanted: boolean };
  last: { home?: boolean; distance_km?: number; heading_home?: boolean; eta_min?: number; wifi_home?: boolean; charging?: boolean;
          bedtime_mode?: boolean; next_alarm?: number; watch_connected?: boolean; dials?: { minor: number; rssi: number }[] };
  denied?: string; saved?: boolean;
}
interface EmberPhone {
  status(): Promise<PhoneStatus>;
  setEnabled(o: { source: PhoneSource; on: boolean }): Promise<PhoneStatus>;
  setHomeHere(): Promise<PhoneStatus>;
  setRadius(o: { meters: number }): Promise<PhoneStatus>;
  setHomeWifi(): Promise<PhoneStatus>;
  sendNow(): Promise<PhoneStatus>;
  openSettings(): Promise<void>;
}
const Phone = registerPlugin<EmberPhone>("EmberPhone");
/** Older app builds don't have the plugin: they reject, and the section stays hidden. */
export const phoneStatus = () => { if (!isApp()) return Promise.reject(new Error("app only")); return Phone.status(); };
export const phoneSetEnabled = (source: PhoneSource, on: boolean) => Phone.setEnabled({ source, on });
export const phoneSetHomeHere = () => Phone.setHomeHere();
export const phoneSetRadius = (meters: number) => Phone.setRadius({ meters });
export const phoneSetHomeWifi = () => Phone.setHomeWifi();
export const phoneSendNow = () => Phone.sendNow();
export const phoneOpenSettings = () => Phone.openSettings();
