import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../ctx";
import Fold from "../components/Fold";
import ShowMore from "../components/ShowMore";
import Tip from "../components/Tip";
import HaConnectionCard from "./HaConnection";
import DialSetup, { type DialsInfo, dialSummary } from "./DialSetup";
import CloudAccounts from "./CloudAccounts";

interface Ha { state: string; broker: string | null; prefix: string; entities: number; commands_handled: number; ignored_retained?: number; object_ids: Record<string, string>;
  host: string; port: number; username: string; password_saved: boolean; from_env: boolean }
interface Ent { entity_id: string; name: string; device: string }

export default function Integrations() {
  const { snap, say } = useApp();
  const auth = useAuth();
  const loc = useLocation();
  const [ha, setHa] = useState<Ha | null>(null);
  const [form, setForm] = useState({ host: "", port: 1883, username: "", password: "", prefix: "homeassistant" });
  const [ents, setEnts] = useState<Ent[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [dials, setDials] = useState<DialsInfo | null>(null);
  const load = () => {
    api.get<Ha>("/api/integrations/ha").then(h => { setHa(h); setForm(f => ({ ...f, host: h.host, port: h.port, username: h.username, password: "", prefix: h.prefix })); }).catch(() => {});
    api.get<Ent[]>("/api/integrations/ha/entities").then(setEnts).catch(() => {});
  };
  useEffect(() => { if (auth.isOwner) { load(); api.get<DialsInfo>("/api/dials").then(setDials).catch(() => {}); } }, [auth.isOwner]);
  useEffect(() => { if (!ha || ha.state === "connected" || ha.state === "off") return; const t = window.setTimeout(load, 2500); return () => clearTimeout(t); }, [ha]);
  // links like /studio/integrations#dials land on that section
  useEffect(() => { if (loc.hash) { const t = window.setTimeout(() => document.getElementById(loc.hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "start" }), 200); return () => clearTimeout(t); } }, [loc.hash, !!ha]);
  if (!snap) return null;
  const header = <div><div className="k">Configure</div><h1 style={{ fontSize: 32 }}>Integrations</h1>
    <p className="mute" style={{ fontSize: 13 }}>Home Assistant, bedside dials and cloud accounts: where Ember &amp; Tide reads devices from, and where it shows up.</p></div>;
  if (!auth.isOwner) return <>{header}<div className="box"><span className="mute">Only the owner can change integrations.</span></div></>;

  const save = async (off = false) => {
    setBusy(true);
    const body = { ...form, keep_password: form.password === "" && !!ha?.password_saved };
    try { setHa(await api.put<Ha>("/api/integrations/ha", off ? { ...body, host: "" } : body)); setForm(f => ({ ...f, password: "" })); say(off ? "Turned off" : "Saved. Connecting…"); }
    catch (e) { say((e as Error).message); }
    setBusy(false); window.setTimeout(load, 1500);
  };
  // states: off · starting · connected · or a plain sentence saying what's wrong (the bridge keeps retrying)
  const st = ha?.state;
  const problem = st && !["connected", "off", "starting"].includes(st) ? st.charAt(0).toUpperCase() + st.slice(1) : null;
  const stateColor = st === "connected" ? "var(--good)" : st === "off" ? "var(--mute)" : problem ? "var(--bad)" : "var(--warm)";
  const stateText = !ha ? "…" : st === "connected" ? `Connected to ${ha.broker}` : st === "off" ? "Off" : st === "starting" ? "Connecting…" : "Not connected";
  const goMqtt = (e: React.MouseEvent) => { e.preventDefault(); document.getElementById("mqtt")?.scrollIntoView({ behavior: "smooth" }); };

  return (
    <>
      {header}
      <h2 className="intsec" id="ha">Home Assistant</h2>
      <HaConnectionCard />
      <Fold id="studio.integrations.entities" className="box" title="What Ember & Tide shows in Home Assistant" keepSummary attention={!!problem}
        summary={<span style={{ color: stateColor }}>● {st === "connected" ? `${ents.length || ha?.entities || 0} entities published` : st === "off" ? "off" : stateText}</span>}>
        <span className="mute" style={{ fontSize: 13 }}>Each person's side appears in Home Assistant (a thermostat, flash buttons, tonight's plan and more), for dashboards, voice, automations and the bedside dials.</span>
        {st !== "connected" && <p className="dial-note" style={{ margin: 0 }}>{st === "off" || !ha ? "Not turned on yet. " : `${stateText}. `}It goes through your MQTT broker (usually the Mosquitto add-on):{" "}
          <a href="#mqtt" className="link" onClick={goMqtt}>set it up under Advanced (MQTT)</a>.</p>}
        {st === "connected" && <span className="mute" style={{ fontSize: 12.5 }}>{ha!.commands_handled} {ha!.commands_handled === 1 ? "command" : "commands"} from Home Assistant so far.</span>}
        {ents.length > 0 && <details className="conn-details"><summary className="mute">Show the {ents.length} entities</summary>
          <ShowMore items={ents} limit={10} noun="entities" render={shown => <table className="t"><tbody>
            {shown.map(e => <tr key={e.entity_id}><td>{e.name}</td><td className="ent-id"><code style={{ fontSize: 12 }}>{e.entity_id}</code></td></tr>)}
          </tbody></table>} />
          <span className="faint" style={{ fontSize: 12 }}>IDs stay fixed, so renaming someone never breaks dials or automations.</span></details>}
        {st === "connected" && (confirmRemove
          ? <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}><button className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={async () => { const r = await api.post<{ removed: number }>("/api/integrations/ha/remove-entities"); say(`Removed ${r.removed} from Home Assistant`); setConfirmRemove(false); }}>Remove them from Home Assistant?</button><button className="pill" onClick={() => setConfirmRemove(false)}>Keep</button></div>
          : <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}><button className="pill" onClick={async () => { await api.post("/api/integrations/ha/announce"); say("Published again"); }}>Publish again</button><button className="pill" onClick={() => setConfirmRemove(true)}>Remove from Home Assistant</button></div>)}
      </Fold>

      <h2 className="intsec" id="dials">Bedside dials</h2>
      <Fold id="studio.integrations.dials" className="box" title="Bedside dials" keepSummary defaultOpen summary={dialSummary(dials)}>
        <DialSetup people={snap.people} embedded />
      </Fold>

      <h2 className="intsec" id="cloud">Cloud accounts</h2>
      <CloudAccounts />

      <h2 className="intsec" id="mqtt">Advanced (MQTT)</h2>
      <Fold id="studio.integrations.mqtt" className="box" title="MQTT broker for Home Assistant" keepSummary attention={!!problem}
        summary={<span style={{ color: stateColor }}>● {stateText}</span>}>
        <span className="mute" style={{ fontSize: 13 }}>How Ember &amp; Tide reaches Home Assistant: the broker it already uses (usually the Mosquitto add-on). Give Ember &amp; Tide its own login.
          <Tip label="How the bridge works" text="Entities appear on their own (MQTT discovery), one device per person. For the login: in Home Assistant add a user, or a login in the Mosquitto add-on's settings, and enter it here." /></span>
        {ha?.from_env ? <span className="faint" style={{ fontSize: 12.5 }}>Set by ET_HA_MQTT_URL on the server.</span> : <div className="authform">
          <div className="row" style={{ gap: 8 }}>
            <label className="field" style={{ flex: 1 }}><span>Broker address</span><input type="text" value={form.host} placeholder="homeassistant.local" title="Host name or address, e.g. homeassistant.local" onChange={e => setForm({ ...form, host: e.target.value })} /></label>
            <label className="field" style={{ width: 96 }}><span>Port</span><input type="number" value={form.port} onChange={e => setForm({ ...form, port: parseInt(e.target.value || "1883") })} /></label>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <label className="field" style={{ flex: 1 }}><span>Username</span><input type="text" autoComplete="off" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></label>
            <label className="field" style={{ flex: 1 }}><span>Password</span><input type="password" autoComplete="new-password" value={form.password} placeholder={ha?.password_saved ? "Saved · type to replace" : ""} onChange={e => setForm({ ...form, password: e.target.value })} /></label>
          </div>
          <details><summary className="mute" style={{ fontSize: 13, cursor: "pointer" }}>Discovery prefix</summary>
            <label className="field" style={{ marginTop: 8 }}><span>Discovery prefix</span><input type="text" value={form.prefix} onChange={e => setForm({ ...form, prefix: e.target.value })} /><small className="mute">Leave as "homeassistant" unless you changed it in HA's MQTT settings.</small></label></details>
          <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}>
            <button className="btn pri" disabled={busy || !form.host} onClick={() => save()}>{st === "connected" ? "Save changes" : "Save & connect"}</button>
            {ha && ha.state !== "off" && <button className="btn" disabled={busy} onClick={() => save(true)}>Turn off</button>}
          </div>
        </div>}
        {problem && <p className="ha-problem" role="status">{problem}. It keeps trying on its own; fix the details above and press Save &amp; connect to try right away.</p>}
        {!!ha?.ignored_retained && <span className="mute" style={{ fontSize: 12.5 }}>
          Ignored {ha.ignored_retained} old {ha.ignored_retained === 1 ? "command" : "commands"} left waiting on the broker after a reconnect, so nothing from before was replayed.</span>}
      </Fold>
    </>
  );
}
