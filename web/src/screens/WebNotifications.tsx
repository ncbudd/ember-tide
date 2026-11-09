import { useEffect, useState } from "react";
import { api } from "../api";
import { currentEndpoint, disableWebPush, enableWebPush, isIOS, notifPermission, webPushInfo, webPushSupport, type WebPushInfo } from "../pwa";

/** iPhone: Share → Add to Home Screen → open from the icon. Drawn, not described, so it's quick to follow. */
function InstallGuide() {
  return (
    <ol className="ig" aria-label="Add Ember & Tide to your Home Screen">
      <li><span className="ig-pic" aria-hidden><svg viewBox="0 0 24 24"><path d="M12 3v12M7.5 7.5 12 3l4.5 4.5" /><path d="M8 10H6v10h12V10h-2" /></svg></span>
        <span>Tap <b>Share</b> in Safari's toolbar<small className="mute">the square with an arrow, at the bottom (or top right on iPad)</small></span></li>
      <li><span className="ig-pic" aria-hidden><svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="4" /><path d="M12 8.5v7M8.5 12h7" /></svg></span>
        <span>Choose <b>Add to Home Screen</b><small className="mute">scroll the list down if you don't see it, then tap Add</small></span></li>
      <li><span className="ig-pic ig-icon" aria-hidden><img src="/apple-touch-icon.png" alt="" width={34} height={34} /></span>
        <span>Open <b>Ember &amp; Tide</b> from your Home Screen<small className="mute">sign in there once; notifications can be turned on from that icon</small></span></li>
    </ol>
  );
}

/** Honest about what an iPhone web app can't do, and what to use instead. */
export function IphoneLimits() {
  return (
    <div className="card iphone-limits">
      <b style={{ fontWeight: 500 }}>What the iPhone web app can't do</b>
      <ul>
        <li>Read Apple Health (so no Apple Watch data yet)</li>
        <li>Know when you're home or on your way (no background location)</li>
        <li>Find the bedside dial over Bluetooth</li>
      </ul>
      <b style={{ fontWeight: 500 }}>What works instead</b>
      <ul>
        <li>An Oura ring connects through Oura's cloud: sleep stages, heart rate and temperature</li>
        <li>The bed's own sensors start your night when you get in, no phone needed</li>
        <li>A calendar link (iCloud, Google, Fastmail) can tell it about late evenings and early starts. The person who set this up adds it in Studio</li>
      </ul>
      <small className="mute">The iPhone app, when it arrives, will read Apple Health.</small>
    </div>
  );
}

/** Web Push for a browser or the iPhone Home Screen app (the Android app uses its own native notifications). */
export default function WebNotifications({ person, onState }: { person: string; onState?: (on: boolean) => void }) {
  const sup = webPushSupport();
  const [info, setInfo] = useState<WebPushInfo | null>(null);
  const [mine, setMine] = useState<boolean>(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = async () => {
    try {
      const i = await webPushInfo(person);
      setInfo(i);
      const ep = await currentEndpoint();
      const on = !!ep && notifPermission() === "granted" && i.devices.length > 0;
      setMine(on); onState?.(on);
    } catch { setInfo(null); }
  };
  useEffect(() => { if (sup === "ok") void load(); }, [person, sup]);
  const run = async (label: string, f: () => Promise<unknown>) => {
    setBusy(label); setMsg(null);
    try { await f(); } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); void load(); }
  };

  if (sup === "install") return (
    <div className="webpush">
      <p>On iPhone, notifications work once Ember &amp; Tide is on your Home Screen. It takes three taps:</p>
      <InstallGuide />
      <small className="mute">Needs iOS 16.4 or newer.</small>
    </div>
  );
  if (sup === "old-ios") return <p className="mute">Notifications need iOS 16.4 or newer. Update the iPhone (Settings → General → Software Update) and they'll be offered here.</p>;
  if (sup === "insecure") return <p className="mute">Notifications need the secure (https) address of your server.</p>;
  if (sup === "unsupported") return <p className="mute">This browser can't show notifications from websites.</p>;
  if (sup === "app") return null;
  if (!info) return null;
  if (!info.available) return <p className="mute">Notifications from the web app aren't available on this server.</p>;
  const denied = notifPermission() === "denied";
  return (
    <div className="webpush">
      {mine ? <>
        <div className="row"><span><b style={{ fontWeight: 500 }}>On for this {isIOS() ? "iPhone" : "browser"}</b></span>
          <button className="pill" disabled={!!busy} onClick={() => run("test", async () => { await api.post(`/api/notify/me/push/test?person=${encodeURIComponent(person)}`); setMsg("Sent. It should arrive in a few seconds."); })}>{busy === "test" ? "Sending…" : "Send a test"}</button></div>
        <button className="link" disabled={!!busy} onClick={() => run("off", () => disableWebPush(person))}>Turn off on this {isIOS() ? "iPhone" : "browser"}</button>
      </> : denied ? (
        <p className="mute">Notifications are blocked for this site. {isIOS() ? "Turn them on in the iPhone's Settings → Notifications → Ember & Tide." : "Allow them in the browser's site settings."}</p>
      ) : (
        <button className="btn pri" disabled={!!busy} onClick={() => run("on", async () => { const r = await enableWebPush(person); if (r === "denied") setMsg("Not allowed. You can turn them on later in Settings."); })}>
          {busy === "on" ? "Asking…" : "Turn on notifications"}</button>
      )}
      {msg && <p className="mute" role="status">{msg}</p>}
    </div>
  );
}
