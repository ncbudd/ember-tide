import { useEffect, useState } from "react";
import { api } from "../api";
import Fold from "../components/Fold";
import { canShare, publicOrigin, shareText } from "../native";
import { useAuth } from "../auth";
import { useApp } from "../ctx";

interface Sess { id: string; created: number; seen: number; agent: string | null; ip: string | null; current: boolean }
/** A limited key the phone made for its widget and tile, or for the watch (never the sign-in itself). */
export interface DeviceKey { id: string; name: string; scope: "watch" | "phone"; person: string | null; created: number; last_used: number | null; username: string | null; this_device: boolean }
export const deviceKeyName = (k: DeviceKey) => k.scope === "watch" ? "Watch" : "Phone widget and tile";

/** "Phone on Android · Firefox" from a user-agent, good enough to recognise your own devices. */
function deviceName(ua: string | null): string {
  if (!ua) return "Unknown device";
  const os = /iPhone|iPad/.test(ua) ? (ua.includes("iPad") ? "iPad" : "iPhone") : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "Device";
  const br = /Firefox\//.test(ua) ? "Firefox" : /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /python|curl|httpx/i.test(ua) ? "script" : "browser";
  return `${os} · ${br}`;
}
const ago = (ts: number) => {
  const s = Date.now() / 1000 - ts;
  return s < 120 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
};

export default function Account() {
  const auth = useAuth();
  const [open, setOpen] = useState<"pw" | "devices" | null>(null);
  const [cur, setCur] = useState(""), [nw, setNw] = useState(""), [nw2, setNw2] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [sess, setSess] = useState<Sess[]>([]);
  const [keys, setKeys] = useState<DeviceKey[]>([]);
  const loadSess = () => {
    api.get<Sess[]>("/api/auth/me/sessions").then(setSess).catch(() => {});
    api.get<DeviceKey[]>("/api/auth/me/device-tokens").then(setKeys).catch(() => setKeys([]));
  };
  useEffect(() => { if (open === "devices") loadSess(); }, [open]);
  if (!auth.state.enabled) {
    return <Fold id="settings.account" title="Account" summary="Sign-in is off">
      <div className="card list"><p className="mute" style={{ fontSize: 13, padding: "10px 0" }}>Sign-in is turned off on this server, so anyone on your home network can use it. Turn it on before opening it to the internet (Studio → People &amp; access says how).</p></div>
    </Fold>;
  }
  const me = auth.me!;
  const { snap } = useApp();
  const partnerP = snap?.people.find(p => p.id !== me.person);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const invite = async () => {
    if (!partnerP) return;
    try {
      const r = await api.post<{ path: string }>("/api/auth/invites", { person: partnerP.id, role: "sleeper" });
      const link = `${publicOrigin()}${r.path}`;
      setInviteLink(link);
      const text = `Here's your link for Ember & Tide (our bed). Open it, pick a username and password, and you're in: ${link}`;
      if (canShare()) await shareText("Ember & Tide", text);   // the link also stays on screen
    } catch (e) { setMsg((e as Error).message); }
  };
  const changePw = async () => {
    setMsg(null);
    if (nw !== nw2) { setMsg("The two new passwords don't match."); return; }
    try {
      const r = await api.post<{ signed_out_elsewhere: number }>("/api/auth/me/password", { current: cur, new: nw });
      setMsg(`Password changed.${r.signed_out_elsewhere ? ` Signed out ${r.signed_out_elsewhere} other device${r.signed_out_elsewhere > 1 ? "s" : ""}.` : ""}`);
      setCur(""); setNw(""); setNw2(""); setOpen(null);
    } catch (e) { setMsg((e as Error).message); }
  };
  const roleText = me.role === "owner" ? "Owner · can set up devices and invite people" : me.role === "viewer" ? "Can look, can't change anything" : "Your side, plus whatever your partner shares";
  return <Fold id="settings.account" title="Account" summary={`Signed in as ${me.name}`}>
    <div className="card list">
      <div className="row"><span>Signed in as <b style={{ fontWeight: 500 }}>{me.name}</b><br /><small className="mute">{roleText}</small></span>
        <button className="pill" onClick={() => auth.signOut()}>Sign out</button></div>
      {me.role === "owner" && partnerP && <>
        <button className="row rowbtn" onClick={invite}><span>Invite {partnerP.name}<br /><small className="mute">Sends a one-time sign-up link. Only people you invite can make an account.</small></span><span className="mute">›</span></button>
        {inviteLink && <div style={{ display: "grid", gap: 6, padding: "6px 0 10px" }}>
          <code className="secret">{inviteLink}</code>
          <div className="chips"><button className="chip" onClick={async () => { try { await navigator.clipboard.writeText(inviteLink); setMsg("Link copied. Paste it into a message."); } catch { setMsg("Select the link and copy it."); } }}>Copy link</button>
            {canShare() && <button className="chip" onClick={() => shareText("Ember & Tide", inviteLink)}>Share…</button>}</div>
          <small className="mute">Works once, for 7 days. Their account then stays until you remove it in Studio → People &amp; access.</small>
        </div>}
      </>}
      {me.kind === "user" && <>
        <button className="row rowbtn" onClick={() => setOpen(open === "pw" ? null : "pw")}><span>Change password</span><span className="mute">{open === "pw" ? "▴" : "▾"}</span></button>
        {open === "pw" && <div className="authform" style={{ padding: "4px 0 12px" }}>
          <label className="field"><span>Current password</span><input type="password" autoComplete="current-password" value={cur} onChange={e => setCur(e.target.value)} /></label>
          <label className="field"><span>New password</span><input type="password" autoComplete="new-password" value={nw} onChange={e => setNw(e.target.value)} /></label>
          <label className="field"><span>New password again</span><input type="password" autoComplete="new-password" value={nw2} onChange={e => setNw2(e.target.value)} /></label>
          <button className="btn pri" disabled={!cur || !nw || !nw2} onClick={changePw}>Change password</button>
          <small className="mute">Changing it signs out your other devices.</small>
        </div>}
        <button className="row rowbtn" onClick={() => setOpen(open === "devices" ? null : "devices")}><span>Signed-in devices</span><span className="mute">{open === "devices" ? "▴" : "▾"}</span></button>
        {open === "devices" && <div style={{ display: "grid", gap: 2, paddingBottom: 8 }}>
          {sess.map(s => (
            <div className="row" key={s.id}><span>{deviceName(s.agent)}{s.current && <small style={{ color: "var(--good)" }}> · this one</small>}<br /><small className="mute">last used {ago(s.seen)}{s.ip ? ` · ${s.ip}` : ""}</small></span>
              {!s.current && <button className="pill" onClick={async () => { await api.del(`/api/auth/me/sessions/${s.id}`); loadSess(); }}>Sign out</button>}</div>
          ))}
          {sess.length > 1 && <button className="btn" onClick={async () => { await api.post("/api/auth/me/sessions/others"); loadSess(); }}>Sign out everywhere else</button>}
          {keys.length > 0 && <>
            <small className="mute" style={{ paddingTop: 10 }}>Your watch and home-screen widget. They can only show your side and change its temperature, flashes and tonight. Signing out of the phone that set one up ends it.</small>
            {keys.map(k => (
              <div className="row" key={k.id}><span>{deviceKeyName(k)}{k.this_device && <small className="mute"> · from this phone</small>}<br /><small className="mute">{k.last_used ? `last used ${ago(k.last_used)}` : "not used yet"}</small></span>
                <button className="pill" onClick={async () => { try { await api.del(`/api/auth/me/device-tokens/${k.id}`); setMsg(`${deviceKeyName(k)} key revoked. Opening the app on that phone makes a new one.`); } catch (e) { setMsg((e as Error).message); } loadSess(); }}>Revoke</button></div>
            ))}
          </>}
        </div>}
      </>}
      {msg && <p className="mute" role="status" style={{ fontSize: 13, padding: "8px 0" }}>{msg}</p>}
    </div>
  </Fold>;
}
