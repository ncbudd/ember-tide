import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import Fold from "../components/Fold";
import Tip from "../components/Tip";

/**
 * Studio → Integrations → Cloud accounts. Self-contained so any Integrations layout can mount it.
 * - Wearable makers' clouds (Oura, Withings, WHOOP, Polar): the owner enters each app's client id and secret once;
 *   people connect their own account from the phone (Settings → Watch or ring, or first-time setup).
 * - Anything else: an inbound webhook (a secret address that takes JSON) or a JSON address polled every N minutes.
 * Server: core/embertide/cloud/, cloud_api.py. Secrets never come back from the server.
 */

export interface Provider { id: string; name: string; gives?: string; docs_url?: string; scopes?: string; redirect_uri?: string; configured: boolean; connected: Record<string, boolean>; available: boolean; why?: string }
interface Hook { id: string; name: string; person: string; metric: string; items_path: string; value_path: string; time_path: string; unit: string; scale: number; enabled: boolean; url: string; last_ts?: number; last_ok?: boolean; last_error?: string | null; last_stored?: number }
interface Poll extends Omit<Hook, "url"> { url: string; headers: Record<string, string>; interval_min: number }

const METRICS: [string, string][] = [["hr", "Heart rate (bpm)"], ["hrv", "HRV (ms)"], ["skin_temp", "Skin temperature"], ["body_temp", "Body temperature"],
  ["resp_rate", "Breathing rate"], ["spo2", "Blood oxygen (%)"], ["resting_hr", "Resting heart rate"], ["temp_dev", "Temperature vs normal (°C)"],
  ["readiness", "Readiness / recovery score"], ["weight_kg", "Weight"], ["steps", "Steps"], ["active_kcal", "Active calories"],
  ["exercise_min", "Exercise minutes"], ["caffeine_mg", "Caffeine (mg)"]];

const ago = (t?: number) => { if (!t) return "never"; const s = Date.now() / 1000 - t; return s < 120 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; };

export default function CloudAccounts() {
  const [list, setList] = useState<Provider[] | null>(null);
  const load = () => api.get<{ providers: Provider[] }>("/api/cloud/providers").then(r => setList(r.providers)).catch(() => setList(null));
  useEffect(() => { void load(); }, []);
  useEffect(() => { const id = location.hash.slice(1); if (list && id) document.getElementById(id)?.scrollIntoView({ block: "start" }); }, [list != null]);
  if (!list) return null;
  const avail = list.filter(p => p.available), not = list.filter(p => !p.available);
  return (
    <div id="cloud" className="cloud-accounts" style={{ display: "grid", gap: 12, scrollMarginTop: 16 }}>
      <Fold id="studio.integrations.cloud" className="box" title="Cloud accounts" keepSummary defaultOpen={!avail.some(p => p.configured)}
        summary={`${avail.filter(p => p.configured).length} of ${avail.length} set up`}>
        <span className="mute" style={{ fontSize: 13 }}>Data straight from a wearable maker's cloud, for what the phone can't pass on. Set each one up once here; then each person connects their own account from the phone.
          <Tip label="How it polls" text="After the person wakes (once the device has synced), then every hour; never during the night. Readings that also arrive through Health Connect or Apple Health are stored once." /></span>
        {avail.map(p => <ProviderRow key={p.id} p={p} onSaved={load} />)}
        {not.length > 0 && <details><summary className="mute" style={{ fontSize: 13, cursor: "pointer", minHeight: 36 }}>Not available as a cloud account ({not.length})</summary>
          <ul className="mute" style={{ fontSize: 13, paddingLeft: 18, display: "grid", gap: 4 }}>{not.map(p => <li key={p.id}><b style={{ fontWeight: 500 }}>{p.name}</b>: {p.why}</li>)}</ul></details>}
      </Fold>
      <Hooks />
      <Polls />
    </div>
  );
}

function ProviderRow({ p, onSaved }: { p: Provider; onSaved: () => void }) {
  const { snap } = useApp();
  const [open, setOpen] = useState(false);
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [saved, setSaved] = useState<{ client_id: string; secret_saved: boolean } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (open && !saved) api.get<{ client_id: string; secret_saved: boolean }>(`/api/integrations/cloud/${p.id}`).then(a => { setSaved(a); setCid(a.client_id); }).catch(() => {}); }, [open]);
  const who = (snap?.people ?? []).map(x => `${x.name}: ${p.connected[x.id] ? "connected" : "not connected"}`).join(" · ");
  const save = async (clear = false) => {
    setMsg(null);
    try {
      const a = await api.put<{ client_id: string; secret_saved: boolean }>(`/api/integrations/cloud/${p.id}`, clear ? { client_id: "" } : { client_id: cid.trim(), client_secret: secret || null });
      setSaved(a); setSecret(""); setMsg(clear ? "Removed" : "Saved"); onSaved();
    } catch (e) { setMsg((e as Error).message); }
  };
  return (
    <div id={p.id} className="cloud-row" style={{ scrollMarginTop: 16 }}>
      <button type="button" className="row rowbtn" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span><b style={{ fontWeight: 500 }}>{p.name}</b><br /><small className="mute">{p.configured ? who : "not set up"}</small></span>
        <span className="mute">{open ? "‹" : "›"}</span>
      </button>
      {open && <div className="authform" style={{ display: "grid", gap: 8, padding: "4px 0 12px" }}>
        {p.gives && <span className="mute" style={{ fontSize: 13 }}>Brings {p.gives}.</span>}
        <ol className="mute" style={{ fontSize: 13, margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>
          <li>Create an app at {p.docs_url ? <a href={p.docs_url} target="_blank" rel="noreferrer">{p.docs_url.replace(/^https?:\/\//, "")}</a> : `${p.name}'s developer site`} with this exact redirect address:<br /><code style={{ userSelect: "all", wordBreak: "break-all" }}>{p.redirect_uri}</code></li>
          {p.scopes && <li>Allow: <b>{p.scopes}</b></li>}
          <li>Paste its client id and secret here.</li>
        </ol>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: "1 1 200px" }}><span>Client id</span><input type="text" autoComplete="off" value={cid} onChange={e => setCid(e.target.value)} /></label>
          <label className="field" style={{ flex: "1 1 200px" }}><span>Client secret</span><input type="password" autoComplete="new-password" value={secret} placeholder={saved?.secret_saved ? "Saved · type to replace" : ""} onChange={e => setSecret(e.target.value)} /></label>
        </div>
        <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
          <button className="btn pri" disabled={!cid.trim() || (!secret && !saved?.secret_saved)} onClick={() => save()}>Save</button>
          {p.configured && <button className="btn" onClick={() => save(true)}>Remove</button>}
          {msg && <span className="mute" role="status" style={{ fontSize: 13 }}>{msg}</span>}
        </div>
        {p.id === "oura" && <span className="faint" style={{ fontSize: 12 }}>Oura already in Home Assistant (the Oura integration)? Its sensors can be added in Studio → Devices instead; this connection brings the night's detail that Home Assistant doesn't keep.</span>}
      </div>}
    </div>
  );
}

const blankMap = { metric: "hr", items_path: "", value_path: "value", time_path: "", unit: "", scale: 1 };

function MapFields({ v, set }: { v: typeof blankMap; set: (x: typeof blankMap) => void }) {
  return <>
    <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
      <label className="field" style={{ flex: "1 1 180px" }}><span>What it is</span><select value={v.metric} onChange={e => set({ ...v, metric: e.target.value })}>{METRICS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      {(v.metric === "skin_temp" || v.metric === "body_temp") && <label className="field" style={{ width: 110 }}><span>Unit</span><select value={v.unit} onChange={e => set({ ...v, unit: e.target.value })}><option value="">°C</option><option value="F">°F</option></select></label>}
      <label className="field" style={{ width: 110 }}><span>Multiply by</span><input type="number" step="any" value={v.scale} onChange={e => set({ ...v, scale: parseFloat(e.target.value) || 1 })} /></label>
    </div>
    <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
      <label className="field" style={{ flex: "1 1 140px" }}><span>List of readings at</span><input type="text" placeholder="(the whole body)" value={v.items_path} onChange={e => set({ ...v, items_path: e.target.value })} /></label>
      <label className="field" style={{ flex: "1 1 140px" }}><span>Value at</span><input type="text" value={v.value_path} onChange={e => set({ ...v, value_path: e.target.value })} /></label>
      <label className="field" style={{ flex: "1 1 140px" }}><span>Time at</span><input type="text" placeholder="(when it arrives)" value={v.time_path} onChange={e => set({ ...v, time_path: e.target.value })} /></label>
    </div>
    <span className="fld-hint faint" style={{ fontSize: 12 }}>Paths like <code>data.readings</code>, <code>bpm</code>, <code>$.items[0].value</code>. Times can be seconds, milliseconds or ISO text.</span>
  </>;
}

function Hooks() {
  const { snap } = useApp();
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [form, setForm] = useState<{ name: string; person: string } & typeof blankMap | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api.get<{ hooks: Hook[] }>("/api/integrations/hooks").then(r => setHooks(r.hooks)).catch(() => {});
  useEffect(() => { void load(); }, []);
  const people = snap?.people ?? [];
  const pname = (id: string) => people.find(p => p.id === id)?.name ?? id;
  return (
    <Fold id="studio.integrations.hooks" className="box" title="Webhooks (send data in)" keepSummary summary={hooks.length ? `${hooks.length} set up` : "none"}>
      <span className="mute" style={{ fontSize: 13 }}>For a service or gadget that can send JSON somewhere (an automation app, a script, another hub). Each webhook gets its own secret address; anyone with the address can send readings, so keep it private.</span>
      {hooks.map(h => <div key={h.id} className="row" style={{ alignItems: "flex-start", gap: 8, flexWrap: "wrap" }}>
        <span style={{ flex: "1 1 260px", minWidth: 0 }}><b style={{ fontWeight: 500 }}>{h.name}</b> <span className="mute">· {pname(h.person)} · {METRICS.find(m => m[0] === h.metric)?.[1]}</span><br />
          <code style={{ fontSize: 11.5, userSelect: "all", wordBreak: "break-all" }}>{h.url}</code><br />
          <small className="mute">Last {ago(h.last_ts)}{h.last_error ? ` · ${h.last_error}` : h.last_ts ? ` · ${h.last_stored ?? 0} new` : ""}</small></span>
        <button className="pill" onClick={async () => { await api.del(`/api/integrations/hooks/${h.id}`); void load(); }}>Remove</button>
      </div>)}
      {form ? <div className="authform" style={{ display: "grid", gap: 8 }}>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: "1 1 180px" }}><span>Name</span><input type="text" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field" style={{ flex: "1 1 140px" }}><span>Whose</span><select value={form.person} onChange={e => setForm({ ...form, person: e.target.value })}>{people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        </div>
        <MapFields v={form} set={x => setForm({ ...form, ...x })} />
        <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
          <button className="btn pri" disabled={!form.name.trim()} onClick={async () => { try { await api.post("/api/integrations/hooks", form); setForm(null); setMsg(null); void load(); } catch (e) { setMsg((e as Error).message); } }}>Make the address</button>
          <button className="btn" onClick={() => setForm(null)}>Cancel</button>{msg && <span className="warn-text">{msg}</span>}
        </div>
      </div> : <button className="pill" style={{ justifySelf: "start" }} onClick={() => setForm({ name: "", person: people[0]?.id ?? "p1", ...blankMap })}>Add a webhook</button>}
    </Fold>
  );
}

function Polls() {
  const { snap } = useApp();
  const [polls, setPolls] = useState<Poll[]>([]);
  const [form, setForm] = useState<{ name: string; person: string; url: string; hname: string; hval: string; interval_min: number } & typeof blankMap | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api.get<{ polls: Poll[] }>("/api/integrations/polls").then(r => setPolls(r.polls)).catch(() => {});
  useEffect(() => { void load(); }, []);
  const people = snap?.people ?? [];
  const pname = (id: string) => people.find(p => p.id === id)?.name ?? id;
  return (
    <Fold id="studio.integrations.polls" className="box" title="Read a JSON address" keepSummary summary={polls.length ? `${polls.length} set up` : "none"}>
      <span className="mute" style={{ fontSize: 13 }}>For a service with a simple API that answers with JSON (a key in a header is fine). Checked every few minutes to every few hours.</span>
      {polls.map(s => <div key={s.id} className="row" style={{ alignItems: "flex-start", gap: 8, flexWrap: "wrap" }}>
        <span style={{ flex: "1 1 260px", minWidth: 0 }}><b style={{ fontWeight: 500 }}>{s.name}</b> <span className="mute">· {pname(s.person)} · every {s.interval_min} min</span><br />
          <small className="mute" style={{ wordBreak: "break-all" }}>{s.url}</small><br />
          <small className="mute">Last {ago(s.last_ts)}{s.last_error ? ` · ${s.last_error}` : s.last_ts ? ` · ${s.last_stored ?? 0} new` : ""}</small></span>
        <span style={{ display: "flex", gap: 6 }}><button className="pill" onClick={async () => { await api.post(`/api/integrations/polls/${s.id}/run`); void load(); }}>Check now</button>
          <button className="pill" onClick={async () => { await api.del(`/api/integrations/polls/${s.id}`); void load(); }}>Remove</button></span>
      </div>)}
      {form ? <div className="authform" style={{ display: "grid", gap: 8 }}>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: "1 1 180px" }}><span>Name</span><input type="text" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field" style={{ flex: "1 1 140px" }}><span>Whose</span><select value={form.person} onChange={e => setForm({ ...form, person: e.target.value })}>{people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label className="field" style={{ width: 120 }}><span>Every (min)</span><input type="number" min={5} max={1440} value={form.interval_min} onChange={e => setForm({ ...form, interval_min: parseInt(e.target.value || "60") })} /></label>
        </div>
        <label className="field"><span>Address</span><input type="text" placeholder="https://" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} /></label>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: "1 1 160px" }}><span>Header name (optional)</span><input type="text" placeholder="Authorization" value={form.hname} onChange={e => setForm({ ...form, hname: e.target.value })} /></label>
          <label className="field" style={{ flex: "1 1 200px" }}><span>Header value</span><input type="password" autoComplete="new-password" value={form.hval} onChange={e => setForm({ ...form, hval: e.target.value })} /></label>
        </div>
        <MapFields v={form} set={x => setForm({ ...form, ...x })} />
        <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
          <button className="btn pri" disabled={!form.name.trim() || !form.url.trim()} onClick={async () => {
            const { hname, hval, ...rest } = form;
            try { await api.post("/api/integrations/polls", { ...rest, headers: hname.trim() ? { [hname.trim()]: hval } : {} }); setForm(null); setMsg(null); void load(); }
            catch (e) { setMsg((e as Error).message); }
          }}>Save</button>
          <button className="btn" onClick={() => setForm(null)}>Cancel</button>{msg && <span className="warn-text">{msg}</span>}
        </div>
      </div> : <button className="pill" style={{ justifySelf: "start" }} onClick={() => setForm({ name: "", person: people[0]?.id ?? "p1", url: "", hname: "", hval: "", interval_min: 60, ...blankMap })}>Add a JSON address</button>}
    </Fold>
  );
}
