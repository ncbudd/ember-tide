import { useEffect, useState } from "react";
import Fold from "../components/Fold";
import {
  phoneOpenSettings, phoneSendNow, phoneSetEnabled, phoneSetHomeHere, phoneSetHomeWifi, phoneSetRadius, phoneStatus,
  type PhoneSource, type PhoneStatus,
} from "../native";

/** Each switch with a one-line reason. Order: what helps the most first. */
const SOURCES: { key: PhoneSource; label: string; why: string }[] = [
  { key: "location", label: "Location", why: "Warms the bed when you're on your way home. Only the distance to home is sent, never where you are." },
  { key: "wifi", label: "Home Wi-Fi", why: "Knows you're home when the phone joins the Wi-Fi it's on now." },
  { key: "calendar", label: "Calendar on this phone", why: "A late evening or an early start tomorrow. Only times are sent, never what the event is." },
  { key: "alarm", label: "Next alarm", why: "Wakes the bed gently in time for the alarm on this phone." },
  { key: "charging", label: "Charging and bedtime mode", why: "Phone on the charger or Do Not Disturb on at night is a sign you're winding down." },
  { key: "watch", label: "Watch connected", why: "Your watch still paired nearby is a small sign you're home." },
  { key: "watch_hr", label: "Watch heart rate (live)", why: "At night your watch (a Galaxy Watch or any Wear OS watch) shares its heart rate every few minutes, so the bed can tell you from a pet or the other side's echo. Only at night, only to your own server; it uses the heart rate your watch already measures." },
  { key: "dial", label: "Bedside dial nearby", why: "At night, with the phone charging, a short Bluetooth listen for the bedside dial: a phone by your dial counts a little toward you being in bed." },
];

/** "Watch heart rate (live)" once it's on: what it did last. */
function watchHrNote(st: PhoneStatus): string {
  const w = st.watch_hr;
  if (!w || !w.last_sent) return w?.perm === "granted" ? "Allowed on the watch. Readings start when your night does." : "On. If you haven't yet: open Ember & Tide on your watch and tap Allow.";
  return `Last readings from your watch ${ago(w.last_sent)} (${w.last_count}). Collected only at night.`;
}

const ago = (ms: number) => {
  const s = (Date.now() - ms) / 1000;
  return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
};

const DENIED: Record<string, string> = {
  location: "Location wasn't allowed.",
  background: "Location is only allowed while the app is open, so home and away stop when it's closed. Choose \"Allow all the time\".",
  calendar: "Calendar wasn't allowed.",
  bluetooth: "Nearby devices wasn't allowed.",
};

/** What the phone said last, in a few words ("home", "12 km away, heading home"). */
function phoneSummary(st: PhoneStatus): string {
  const l = st.last || {};
  const parts: string[] = [];
  if (l.home === true) parts.push("home");
  else if (l.home === false) parts.push(l.distance_km != null ? `${Math.round(l.distance_km * 10) / 10} km away` : "away");
  if (l.heading_home && l.eta_min != null) parts.push(`heading home, about ${Math.round(l.eta_min)} min`);
  if (l.home == null && l.wifi_home) parts.push("on the home Wi-Fi");
  if (l.charging) parts.push("charging");
  if (l.bedtime_mode) parts.push("bedtime mode");
  if (l.dials?.length) parts.push("by a bedside dial");
  return parts.join(" · ");
}

/** "From this phone": switches for what the Android app can tell the bed by itself. Only inside the app. */
export default function PhoneSources({ compact = false }: { compact?: boolean }) {
  const [st, setSt] = useState<PhoneStatus | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [denied, setDenied] = useState<string | null>(null);

  const load = () => phoneStatus().then(setSt).catch(() => setSt(null));
  useEffect(() => {
    load(); const t = setInterval(load, 30_000);
    // back from the system permission pages: show what was allowed
    const vis = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, []);
  if (!st) return null;

  const run = async (label: string, f: () => Promise<PhoneStatus | void>) => {
    setBusy(label); setMsg(null); setDenied(null);
    try {
      const s = await f();
      if (s) { setSt(s); if (s.denied) setDenied(s.denied); if (s.saved === false) setMsg("Couldn't get a location fix. Try again near a window."); }
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
  };
  const toggle = (k: PhoneSource, on: boolean) => run("t:" + k, () => phoneSetEnabled(k, on));
  const anyOn = SOURCES.some(s => st.sources[s.key]?.on);
  const summary = phoneSummary(st);
  const status = !st.signed_in ? "Sign in to send"
    : st.last_error ? "Couldn't send last time, will retry"
    : st.last_sent ? `Last sent ${ago(st.last_sent)}${summary ? " · " + summary : ""}` : anyOn ? "Not sent yet" : "Nothing shared";

  if (compact) {
    // Simple app: the same switches, without the extras (home spot, radius, Wi-Fi name)
    const onNames = SOURCES.filter(s => st.sources[s.key]?.on).map(s => s.label.replace(/ on this phone| connected| nearby| and bedtime mode/, ""));
    return <Fold id="simple.phone" title="From this phone" attention={!!denied} summary={onNames.length ? `${onNames.join(", ")} on` : "Nothing shared"}>
      <div className="card list">
        {anyOn && <p className="mute" style={{ fontSize: 12.5, padding: "10px 0 0" }} role="status">{status}</p>}
        {SOURCES.filter(s => st.sources[s.key] && st.sources[s.key].state !== "unavailable").map(s =>
          <label key={s.key} className="row" style={{ padding: "10px 0", minHeight: 52, cursor: "pointer" }}>
            <span style={{ fontSize: 15 }}>{s.label}</span>
            <input type="checkbox" className="toggle" aria-label={s.label} checked={st.sources[s.key].on} disabled={!!busy}
              onChange={e => toggle(s.key, e.target.checked)} />
          </label>)}
        {denied && <div className="row" style={{ padding: "8px 0" }} role="alert">
          <span style={{ fontSize: 13 }}>{DENIED[denied] ?? "That permission wasn't allowed."}</span>
          <button className="btn" onClick={() => phoneOpenSettings()}>Open settings</button>
        </div>}
        {msg && <p style={{ fontSize: 13, paddingBottom: 8 }} role="status">{msg}</p>}
      </div>
    </Fold>;
  }

  const onNames = SOURCES.filter(s => st.sources[s.key]?.on).map(s => s.label.replace(/ on this phone| connected| nearby| and bedtime mode/, ""));
  const trouble = !!denied || !!st.last_error || SOURCES.some(s => st.sources[s.key]?.on && st.sources[s.key].state !== "ok");
  return <Fold id="settings.phone" title="From this phone" attention={trouble}
    summary={trouble ? "Needs a look" : onNames.length ? `${onNames.join(", ")} on` : "Nothing shared"}>
    <div className="card list">
      <p className="mute" style={{ fontSize: 13, padding: "10px 0 2px" }}>Things this phone can tell the bed by itself. Each is sent only to your own server, and only while its switch is on.</p>
      {anyOn && <div className="row" style={{ minHeight: 52 }}>
        <span style={{ fontSize: 13.5 }} role="status">{status}</span>
        <button className="btn" disabled={!!busy} onClick={() => run("send", phoneSendNow)}>{busy === "send" ? "Sending…" : "Send now"}</button>
      </div>}
      {SOURCES.map(s => {
        const src = st.sources[s.key];
        if (!src) return null;
        const unavailable = src.state === "unavailable";
        const note = unavailable ? "This phone doesn't have Bluetooth."
          : src.on && src.state === "needs_permission" ? "Permission was taken back: switch it off and on to allow it again."
          : src.on && src.state === "needs_background" ? "Works only while the app is open. Allow location \"all the time\" for it to work in the background."
          : src.on && src.state === "needs_watch_permission" ? "On your watch, open Ember & Tide and tap Allow (then \"Allow all the time\")."
          : s.key === "watch_hr" && src.on ? watchHrNote(st)
          : s.why;
        return <div key={s.key}>
          <label className="row" style={{ padding: "10px 0", minHeight: 56, cursor: unavailable ? "default" : "pointer" }}>
            <span style={{ fontSize: 14.5 }}>{s.label}<br /><span className="mute" style={{ fontSize: 12.5 }}>{note}</span></span>
            <input type="checkbox" className="toggle" aria-label={s.label} checked={src.on} disabled={unavailable || !!busy}
              onChange={e => toggle(s.key, e.target.checked)} />
          </label>
          {s.key === "location" && src.on && <div className="pills" style={{ padding: "0 0 10px" }}>
            <button className="pill" disabled={!!busy} onClick={() => run("home", phoneSetHomeHere)}>{busy === "home" ? "Saving…" : st.home_set ? "I'm home: save this spot" : "Save home here"}</button>
            <label className="pill" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>Home is within
              <select aria-label="Home radius" value={st.radius_m} disabled={!!busy} onChange={e => run("r", () => phoneSetRadius(Number(e.target.value)))}>
                {[100, 150, 250, 500, 1000].map(m => <option key={m} value={m}>{m < 1000 ? `${m} m` : "1 km"}</option>)}
              </select>
            </label>
          </div>}
          {s.key === "wifi" && src.on && <div className="pills" style={{ padding: "0 0 10px" }}>
            <span className="mute" style={{ fontSize: 12.5, alignSelf: "center" }}>{st.home_ssid ? `Home Wi-Fi: ${st.home_ssid}` : "Join your home Wi-Fi, then tap Use this Wi-Fi."}</span>
            <button className="pill" disabled={!!busy} onClick={() => run("wifi", phoneSetHomeWifi)}>Use this Wi-Fi</button>
          </div>}
        </div>;
      })}
      {denied && <div className="row" style={{ padding: "8px 0" }} role="alert">
        <span style={{ fontSize: 13 }}>{DENIED[denied] ?? "That permission wasn't allowed."} You can allow it in the phone's settings.</span>
        <button className="btn" onClick={() => phoneOpenSettings()}>Open settings</button>
      </div>}
      {msg && <p style={{ fontSize: 13, paddingBottom: 8 }} role="status">{msg}</p>}
    </div>
  </Fold>;
}
