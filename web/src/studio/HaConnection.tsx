import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import Fold from "../components/Fold";
import Tip from "../components/Tip";

export interface HaConn { configured: boolean; url: string; token: string; devices: number; can_migrate: string[] }
interface Test { ok: boolean; version?: string; location?: string; entities?: number | null; error?: string }

/** One Home Assistant connection for every HA device: the address and one long-lived token, entered once. Devices
 * then just pick entities from a list. The token stays on the server and is never shown again. */
export default function HaConnectionCard({ onChange }: { onChange?: (c: HaConn) => void }) {
  const [c, setC] = useState<HaConn | null>(null);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [editing, setEditing] = useState(false);          // a saved connection shows as a line; Change opens the form
  const load = () => api.get<HaConn>("/api/ha/connection").then(x => { setC(x); setUrl(u => u || x.url); onChange?.(x); }).catch(() => {});
  useEffect(() => { load(); }, []);
  if (!c) return null;
  const describe = (t: Test) => t.ok ? `Connected to Home Assistant${t.version ? ` ${t.version}` : ""}${t.location ? ` (${t.location})` : ""}${t.entities != null ? ` · ${t.entities} entities` : ""}.` : t.error ?? "That didn't work.";
  const test = async () => {
    setBusy(true); setMsg(null);
    try { const t = await api.post<Test>("/api/ha/connection/test", { url: url.trim(), token: token || undefined }); setMsg({ ok: t.ok, text: describe(t) }); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
    setBusy(false);
  };
  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.put<HaConn & { test: Test }>("/api/ha/connection", { url: url.trim(), token: token || undefined });
      setC(r); setToken(""); setEditing(false); onChange?.(r); setMsg({ ok: true, text: `Saved. ${describe(r.test)}` });
    } catch (e) { setMsg({ ok: false, text: (e as Error).message + (c.configured ? " Nothing was changed: devices keep using the saved connection." : "") }); }
    setBusy(false);
  };
  const migrate = async () => {
    try { const r = await api.post<HaConn & { moved: string[] }>("/api/ha/connection/migrate"); setC(r); onChange?.(r); setMsg({ ok: true, text: `${r.moved.length} device${r.moved.length === 1 ? "" : "s"} now use this connection. Their own copies of the token were removed.` }); }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }); }
  };
  const remove = async () => { try { const r = await api.del<HaConn>("/api/ha/connection"); setC(r); onChange?.(r); setUrl(""); setConfirm(false); setMsg({ ok: true, text: "Removed. Devices that used it stop reading until you add it again." }); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); } };

  return (
    <Fold id="studio.integrations.haconn" className="box haconn" title="Connection: read devices from Home Assistant" keepSummary defaultOpen attention={!!msg && !msg.ok}
      summary={c.configured ? `connected · ${c.devices} device${c.devices === 1 ? "" : "s"} use it` : "not set up"}>
      <p className="mute" style={{ fontSize: 13, margin: 0 }}>
        Connect Home Assistant once, then add lights, sensors, phones, calendars and dials from a list of your entities.
        <Tip label="How this differs from the bridge" text="This reads devices from Home Assistant. 'What Ember & Tide shows in Home Assistant' does the opposite: each side appears there for dashboards, voice and dials." /></p>
      {c.configured && <div className="haconn-state">
        <span><span className="dot" aria-hidden="true" />Using <b style={{ fontWeight: 500 }}>{c.url}</b> · token saved</span>
      </div>}
      {c.configured && !editing ? <div className="pills">
        <button className="btn" disabled={busy} onClick={test}>Test</button>
        <button className="pill" onClick={() => setEditing(true)}>Change address or token</button>
        {confirm
          ? <><button className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={remove}>Remove the connection?</button><button className="pill" onClick={() => setConfirm(false)}>Keep</button></>
          : <button className="pill" onClick={() => setConfirm(true)}>Remove</button>}
      </div> : <><div className="haconn-form">
        <label className="fld">Home Assistant address
          <input type="text" value={url} placeholder="http://homeassistant.local:8123" spellCheck={false} onChange={e => setUrl(e.target.value)} /></label>
        <label className="fld">Long-lived access token
          <input type="password" autoComplete="off" value={token} aria-describedby="haconn-token-hint" placeholder={c.configured ? "saved · leave empty to keep it" : "paste it here"} onChange={e => setToken(e.target.value)} /></label>
        <p className="fld-hint" id="haconn-token-hint">Where to get a token: in Home Assistant, your profile (bottom left) → Security → Long-lived access tokens → Create token.<Tip label="About the token" text="Copy it once: Home Assistant won't show it again. Ember & Tide keeps it on the server and never shows it back." /></p>
      </div>
      <div className="pills">
        <button className="btn" disabled={busy || !url.trim() || (!token && !c.configured)} onClick={test}>Test</button>
        <button className="btn pri" disabled={busy || !url.trim() || (!token && !c.configured)} onClick={save}>{c.configured ? "Save changes" : "Save & connect"}</button>
        {c.configured && (confirm
          ? <><button className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={remove}>Remove the connection?</button><button className="pill" onClick={() => setConfirm(false)}>Keep</button></>
          : <button className="pill" onClick={() => setConfirm(true)}>Remove</button>)}
        {c.configured && <button className="pill" onClick={() => { setEditing(false); setToken(""); setUrl(c.url); }}>Cancel</button>}
      </div></>}
      {msg && <p style={{ fontSize: 13, margin: 0, color: msg.ok ? "var(--good)" : "var(--bad)" }} role="status">{msg.text}</p>}
      {c.configured && c.devices === 0 && <p style={{ fontSize: 13, margin: 0 }}>Next: <Link to="/studio/devices">add your devices</Link> (+ Add a device → pick a kind → Home Assistant).</p>}
      {c.configured && c.can_migrate.length > 0 && <div className="haconn-migrate">
        <span style={{ fontSize: 13 }}>{c.can_migrate.length} device{c.can_migrate.length === 1 ? " has" : "s have"} their own copy of this address and token.</span>
        <button className="pill" onClick={migrate}>Use the shared connection</button></div>}
    </Fold>
  );
}
