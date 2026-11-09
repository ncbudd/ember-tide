import { useEffect, useState } from "react";
import { api } from "../api";
import Fold from "../components/Fold";
import { askNotifications, pushDisable, pushEnable, pushStatus, pushTest, type PushStatus } from "../native";

interface Mine { server: string | null; fcm: boolean; devices: { id: string; device: string; ts: number; kind: string }[] }

/**
 * "Notifications on this phone" (Android app only). Native notifications with buttons that act right away, delivered
 * by Google push (nothing to install) or, for anyone who'd rather avoid Google, by the ntfy app.
 */
export default function PhoneNotifications() {
  const [st, setSt] = useState<PushStatus | null>(null);
  const [mine, setMine] = useState<Mine | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    try { setSt(await pushStatus()); } catch { setSt(null); }
    try { setMine(await api.get<Mine>("/api/notify/me/push")); } catch { setMine(null); }
  };
  useEffect(() => { void load(); }, []);
  // ntfy registration finishes in the background: look again for a few seconds
  useEffect(() => {
    if (!st || st.registered || st.error || st.method !== "up") return;
    const t = window.setInterval(load, 1500);
    const stop = window.setTimeout(() => clearInterval(t), 20000);
    return () => { clearInterval(t); clearTimeout(stop); };
  }, [st?.registered, st?.error, st?.method]);
  if (!st || !mine) return null;

  const run = async (label: string, f: () => Promise<unknown>, ok?: string) => {
    setBusy(label); setMsg(null);
    try { await f(); if (ok) setMsg(ok); } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); void load(); }
  };
  const canFcm = mine.fcm && st.fcm_available;
  const canUp = !!mine.server && st.distributors.length > 0;
  const on = st.registered;
  const via = st.method === "fcm" ? "Google push" : "ntfy";

  const trouble = !st.allowed || !!st.error;
  return <Fold id="phone.notify" title="Notifications on this phone" attention={trouble}
    summary={!st.allowed ? "Blocked by Android" : st.error ? "Needs a look" : on ? `On · ${via}` : "Off"}>
    <div className="card list">
      {!st.allowed && <div className="row" style={{ padding: "8px 0" }}>
        <span style={{ fontSize: 13.5, color: "var(--warn)" }}>Android is blocking notifications from Ember &amp; Tide.</span>
        <button className="btn" onClick={() => run("allow", askNotifications)}>Allow</button></div>}

      {!canFcm && !canUp ? (
        !mine.fcm && !mine.server ? <p className="mute" style={{ fontSize: 13, padding: "10px 0" }}>
          Notifications aren't set up on this server yet. The owner turns them on in Studio → Notifications.</p>
        : mine.fcm && !st.fcm_available ? <p className="mute" style={{ fontSize: 13, padding: "10px 0" }}>
          This copy of the app was built without Google push. Update the app to get notifications.</p>
        : <>
          <p style={{ fontSize: 14, padding: "8px 0 0" }}>This server delivers notifications through the free ntfy app.</p>
          <p className="mute" style={{ fontSize: 13 }}>Install ntfy, sign in to {mine.server!.replace(/^https?:\/\//, "")} in it, then come back here.</p>
          <div style={{ padding: "4px 0 8px" }}><a className="btn pri" href="https://play.google.com/store/apps/details?id=io.heckel.ntfy" target="_blank" rel="noreferrer">Get ntfy</a></div>
        </>
      ) : <>
        <div className="row">
          <span>{on ? "On" : "Off"}<br /><small className="mute">{on ? `Through ${via}`
            : st.method && !st.error ? "Setting up…" : "Alerts with buttons that work right from the notification"}</small></span>
          {on ? <button className="btn" disabled={!!busy} onClick={() => run("test", pushTest, "Sent. It should arrive in a moment.")}>{busy === "test" ? "Sending…" : "Send a test"}</button>
            : <button className="btn pri" disabled={!!busy} onClick={() => run("on", () => pushEnable(canFcm ? "fcm" : "up"))}>{busy === "on" ? "Turning on…" : "Turn on"}</button>}
        </div>
        {st.error && <p style={{ fontSize: 13, color: "var(--warn)", paddingBottom: 6 }}>{st.error}</p>}
        {(on || (canFcm && canUp)) && <div className="pills" style={{ padding: "4px 0 8px" }}>
          {canFcm && canUp && st.method !== "up" && <button className="pill" disabled={!!busy} onClick={() => run("up", () => pushEnable("up"))}>Use ntfy instead of Google</button>}
          {canFcm && canUp && st.method === "up" && <button className="pill" disabled={!!busy} onClick={() => run("fcm", () => pushEnable("fcm"))}>Use Google push</button>}
          {on && <button className="pill" disabled={!!busy} onClick={() => run("off", pushDisable)}>Turn off on this phone</button>}
        </div>}
      </>}
      {msg && <p style={{ fontSize: 13, paddingBottom: 8 }} role="status">{msg}</p>}
    </div>
  </Fold>;
}
