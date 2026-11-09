import { useBedFromFoot } from "../components/BedSidePicker";
import { useEffect, useState } from "react";
import { api } from "../api";
import Fold from "../components/Fold";
import Tip from "../components/Tip";
import { HelpLink } from "./Help";
import { useApp } from "../ctx";
import { useAuth } from "../auth";
import { clock, cToF, kgTo, personaColor, toKg, weight, weightUnit } from "../format";
import type { PersonView } from "../types";
import AddDevice from "./AddDevice";
import BedZones from "./BedZones";
import DeviceCatalog, { Calibration } from "./DeviceCatalog";
import { type DialsInfo } from "./DialSetup";
import { HOWTO } from "./deviceInfo";
import { type TileId, isSided, ownerOf, sideWord, tileOf, whoChoices } from "./deviceKinds";
import { type DevType, Purpose, ROLE_LABEL, TuningFields, ValueMap } from "./deviceShared";
import HaPicker from "./HaPicker";
import { CloudLine, DialLine, PhoneLine, type PhoneRow, WearableLine } from "./PhoneRows";
import { type CloudProvider, deviceGroups } from "./deviceGroups";

/** What each health state means, for the tip on a device's status. */
const HEALTH_TIP: Record<string, string> = {
  erratic: "Its readings have been jumping around, so it's set aside: left out of the in-bed vote until it settles down again. \"Trust it anyway\" keeps using it.",
  offline: "Nothing has arrived from it for a while. It's left out of the vote until it reports again.",
  stale: "It reports, but nothing usable (unknown or out of range). It's left out of the vote for now.",
  paused: "You paused it. It's ignored until you press Resume.",
};
const VOTE_TIP = "Each side counts as in bed when the weighted vote of its devices passes 50%. A device's share depends on its weight and on which other devices on that side have something to say right now.";
const REVERSED_TIP = "This sensor is set to read \"on\" the other way round (Edit → How it's read).";

interface Source {
  id: string; name: string; driver: string; role: string; zone: string | null; person: string | null; unit: string; device: string | null; kind: string; kind_label: string;
  group: string | null; tuning: Record<string, unknown>; tuning_resolved: Record<string, unknown>; params: Record<string, string>; enabled: boolean; role_label: string;
  value: number | null; ts: number | null; age_s: number | null; error: string | null; online: boolean; votes: boolean; vote: number | null; vote_weight: number | null;
  health?: { status: "ok" | "erratic" | "error" | "offline" | "stale" | "paused"; why: string; trusted?: boolean };
  changes_today?: number | null; last_change?: number | null; poll_s?: number | null;
  raw_seen?: { value: string; ts: number }[];
}
interface Cal { weights: Record<string, number>; weight_threshold_kg: number; tare_kg: Record<string, number>; person_kg: Record<string, number>; empty_kg?: Record<string, number>; side_kg?: Record<string, number> }
type Part = { id?: string; entity_id?: string; topic?: string; name?: string };

function HowTo({ kind }: { kind: string }) {
  const steps = HOWTO[kind];
  if (!steps?.length) return null;
  return <div className="dev-howto"><span className="k">How to check and calibrate it</span><ol>{steps.map(x => <li key={x}>{x}</li>)}</ol></div>;
}

export default function Devices() {
  useBedFromFoot();                                   // left/right labels as you lie (bed orientation)
  const { snap, say, me } = useApp();
  const auth = useAuth();
  const [sources, setSources] = useState<Source[]>([]);
  const [types, setTypes] = useState<DevType[]>([]);
  const [cal, setCal] = useState<Cal | null>(null);
  const [adding, setAdding] = useState<{ start: TileId | null } | null>(null);
  const [dials, setDials] = useState<DialsInfo | null>(null);
  const [phones, setPhones] = useState<PhoneRow[]>([]);
  const [cloud, setCloud] = useState<CloudProvider[]>([]);
  const loadExtras = () => {
    api.get<DialsInfo>("/api/dials").then(setDials).catch(() => {});
    api.get<PhoneRow[]>("/api/phone-context").then(setPhones).catch(() => setPhones([]));
    api.get<{ providers: CloudProvider[] }>("/api/cloud/providers").then(r => setCloud(r.providers ?? [])).catch(() => setCloud([]));
  };
  const [confirmZero, setConfirmZero] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);       // device whose Edit is open (for a group: its first part)
  const [edit, setEdit] = useState<Record<string, unknown>>({});
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [agree, setAgree] = useState<Record<string, { signal: string; agree: number; samples: number }[]>>({});
  const [loadedAt, setLoadedAt] = useState(Date.now());
  const [haShared, setHaShared] = useState(false);
  const [editConn, setEditConn] = useState<Record<string, string>>({});
  const [editMeta, setEditMeta] = useState({ name: "", device: "", who: "", kind: "custom", role: "presence" });
  const [parts, setParts] = useState<Partial<Record<"left" | "right", Part>>>({});
  const [zoneOn, setZoneOn] = useState<"left" | "right">("left");
  const [, setTick] = useState(0);
  const load = () => api.get<{ sources: Source[]; calibration: Cal; types: DevType[] }>("/api/sources").then(d => { setSources(d.sources); setTypes(d.types); setCal(c => c ?? d.calibration); setLoadedAt(Date.now()); });
  // live: re-read every 3 s while the page is visible (and at once when you come back to it), and keep "N s ago" ticking
  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 3000);
    const tick = setInterval(() => setTick(x => x + 1), 1000);
    const vis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); clearInterval(tick); document.removeEventListener("visibilitychange", vis); };
  }, []);
  useEffect(() => {
    api.get<{ configured: boolean }>("/api/ha/connection").then(c => setHaShared(c.configured)).catch(() => {});
    if (!adding) loadExtras();
    // dials, phones and cloud accounts change on their own (a phone hears a dial, a dial goes offline): keep them
    // fresh while the page is open and visible, without reloading the page
    const t = setInterval(() => { if (!adding && !document.hidden) loadExtras(); }, 20000);
    return () => clearInterval(t);
  }, [adding]);
  useEffect(() => {
    const f = () => snap?.people.forEach(p => api.get<{ signal: string; agree: number; samples: number }[]>(`/api/presence/agreement?person_id=${p.id}`).then(a => setAgree(x => ({ ...x, [p.id]: a }))).catch(() => {}));
    f(); const t = setInterval(f, 60000); return () => clearInterval(t);
  }, [snap?.people.length]);
  if (!snap || !cal) return null;
  const people = snap.people;
  const units = people[0]?.settings.units ?? "F";
  const wu = weightUnit(me?.settings ?? people[0]?.settings);       // lb or kg, as the person looking picked
  const sim = snap.system.driver === "sim";
  const side = (z: string) => people.find(p => p.zone === z);
  const fmt = (s: Source) => s.value == null || !s.enabled ? "—" : s.role === "room_temp" || s.role === "skin_temp" ? `${(units === "F" ? cToF(s.value) : s.value).toFixed(1)}°${units}`
    : s.role === "light" ? ((s.value >= 0.5) !== !!s.tuning_resolved.invert ? "on" : "off") : s.role === "presence" ? ((s.value >= 0.5) !== !!s.tuning_resolved.invert ? "someone there" : "empty")
    : s.role === "home" ? ((s.value >= 0.5) !== !!s.tuning_resolved.invert ? "at home" : "away") : s.role === "location" ? (s.value < 0.3 ? "home" : `${s.value.toFixed(1)} km away`)
    : s.role === "calendar" ? (s.value > 0 ? `event until ${clock(s.value, snap.tz)}` : "nothing this evening") : s.role === "weight" ? weight(s.value, wu)
    : `${Math.round(s.value * 10) / 10} ${(s.tuning_resolved.unit as string) || s.unit}`;
  const typeOf = (id: string) => types.find(t => t.id === id) ?? types.find(t => t.id === "custom")!;
  const ago = (s: Source) => {
    if (s.age_s == null) return "waiting for a reading";
    const a = s.age_s + (Date.now() - loadedAt) / 1000;
    return a < 60 ? `${Math.round(a)} s ago` : a < 5400 ? `${Math.round(a / 60)} min ago` : a < 172800 ? `${Math.round(a / 3600)} h ago` : `${Math.round(a / 86400)} days ago`;
  };
  const share = (zone: string, w: number) => {
    const others = sources.filter(x => x.zone === zone && x.votes && x.enabled && x.vote != null).reduce((t, x) => t + (x.vote_weight ?? 0), 0);
    const total = (cal?.weights.bed ?? 0.5) + others;
    return total > 0 ? Math.round(w / total * 100) : 0;
  };
  const partsOf = (s: Source) => s.group ? sources.filter(x => x.group === s.group).sort((a, b) => (a.zone ?? "").localeCompare(b.zone ?? "")) : [s];
  const needsAttention = (s: Source) => s.enabled && (!!s.error || (!!s.health && !["ok", "paused"].includes(s.health.status)));
  const dot = (list: Source[]) => list.some(x => x.enabled && x.error) ? "bad" : list.some(needsAttention) ? "warn" : list.every(x => !x.enabled) ? "off" : list.some(x => x.age_s == null) ? "off" : "ok";

  // what to add next, in plain words
  const recs: string[] = [];
  if (sources.length || types.length) {
    if (!sources.some(s => s.role === "room_temp" && s.enabled)) recs.push("A bedroom temperature sensor: Autopilot can then tell a warm room from a rhythm that's set too warm.");
    for (const z of ["left", "right"] as const) {
      if (!sources.some(s => s.zone === z && s.votes && s.enabled)) recs.push(`A presence radar or bed scale on the ${sideWord(z).toLowerCase()} side${side(z) && side(z)!.name.toLowerCase() !== z ? ` (${side(z)!.name})` : ""}: the bed strip alone can mistake one sleeper for two.`);
    }
    if (!sources.some(s => s.role === "calendar")) recs.push("A calendar link (Fastmail, iCloud or Google), so late evenings and early starts are known.");
  }

  const connFields = (s: Source): [string, string, string][] => s.driver === "ics" ? [["url", "Calendar link", "Paste a new link to replace it (leave empty to keep it)"]]
    : s.driver === "ha-entity" ? [["entity_id", "Entity ID", "sensor.bedroom_temperature"], ["attribute", "Attribute (optional)", "leave empty to use the state"]]
    : s.driver === "mqtt-topic" ? [["topic", "Topic", "bed-scale/sensor/left/state"], ["json_path", "JSON path (optional)", "targets.0.x"]] : [];
  const connParams = (s: Source, useShared = false) => {
    const p: Record<string, string> = { ...s.params };
    for (const [k] of connFields(s)) {
      const v = (editConn[k] ?? "").trim();
      if (s.driver === "ics" && k === "url") { if (v) p.url = v; continue; }
      if (k in editConn) { if (v) p[k] = v; else delete p[k]; }
    }
    if (useShared) { delete p.url; delete p.token; p.conn = "ha"; }
    return p;
  };
  const saveDevice = async (s: Source, tune: Record<string, unknown>, extra: Partial<Source> = {}) => {
    try { await api.put(`/api/sources/${s.id}`, { ...s, ...extra, tuning: tune }); load(); return true; } catch (e) { say(`Couldn't save: ${(e as Error).message}`); return false; }
  };
  const openEdit = (s: Source, scroll = false) => {
    const rep = partsOf(s)[0];
    if (open === rep.id && !scroll) { setOpen(null); return; }
    setOpen(rep.id); setEdit({ ...rep.tuning_resolved }); setConfirmRemove(null);
    setEditMeta({ name: rep.name, device: rep.device ?? "", who: ownerOf(rep, people), kind: rep.kind, role: rep.role });
    setEditConn(Object.fromEntries(connFields(rep).filter(([k]) => !(rep.driver === "ics" && k === "url")).map(([k]) => [k, rep.params[k] ?? ""])));
    setParts(Object.fromEntries(partsOf(rep).filter(x => x.zone).map(x => [x.zone, { id: x.id, entity_id: x.params.entity_id, topic: x.params.topic, name: x.params.entity_id ?? x.params.topic ?? (x.driver === "sim" ? "Simulated" : undefined) }])));
    setZoneOn("left");
    // its group may be collapsed: the group opens (Fold reveal), then scroll once it's drawn
    if (scroll) { let n = 0; const go = () => { const el = document.getElementById(`dev-${rep.id}`); if (el && el.offsetParent) el.scrollIntoView({ behavior: "smooth", block: "start" }); else if (n++ < 20) window.setTimeout(go, 50); }; window.setTimeout(go, 30); }
  };
  const editById = (id: string) => {
    if (id.startsWith("dial:")) { setAdding({ start: "dials" }); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    const s = sources.find(x => x.id === id);
    if (s) openEdit(s, true);
  };
  const saveEdit = async (s: Source) => {
    const t = typeOf(editMeta.kind);
    const role = (t.roles ?? [t.role]).includes(editMeta.role) ? editMeta.role : (t.roles ?? [t.role])[0];
    const keys = new Set(t.tuning.map(f => f.key));
    const tune = Object.fromEntries(Object.entries(edit).filter(([k]) => keys.has(k)));
    const name = editMeta.name.trim() || s.name;
    const sided = isSided(t.id, role);
    const who = editMeta.who;
    const owner = people.find(p => p.id === who);
    try {
      if (sided && who === "both") {
        if (s.driver === "ics" || s.driver === "sim" && !s.group) { say("This one can't be split into sides."); return; }
        const zp = (["left", "right"] as const).map(z => {
          const p = parts[z];
          if (!p || !(p.entity_id || p.topic || p.id)) return null;
          const base = s.driver === "ha-entity" ? { ...(s.params.conn === "ha" ? { conn: "ha" } : { url: s.params.url, token: s.params.token }), entity_id: p.entity_id ?? "" }
            : s.driver === "mqtt-topic" ? { url: s.params.url, topic: p.topic ?? "", ...(s.params.json_path ? { json_path: s.params.json_path } : {}) } : (sources.find(x => x.id === p.id)?.params ?? s.params);
          return { zone: z, id: p.id, params: base };
        }).filter(Boolean);
        if (!zp.length) { say("Pick what watches at least one side."); return; }
        const gid = s.group ?? Math.random().toString(36).slice(2, 10);
        await api.put(`/api/sources/group/${gid}`, { name, kind: t.id, role, driver: s.driver, device: editMeta.device.trim() || null, tuning: tune, enabled: s.enabled, parts: zp,
          ...(s.params.layout === "two_sensors" && role !== "presence" ? { layout: "zones" } : {}) });   // otherwise the server keeps the layout
      } else {
        // one person / one side: a group shrinks to the part on that side (the other side's part is removed)
        const ps = partsOf(s);
        const keep = (sided && owner ? ps.find(x => x.zone === owner.zone) : null) ?? s;
        const extra: Partial<Source> = { name, device: editMeta.device.trim() || null, kind: t.id, role, group: null,
          zone: sided ? owner?.zone ?? keep.zone : null,
          person: !sided && who !== "both" ? who || null : null,
          ...(connFields(keep).length && keep.id === s.id ? { params: connParams(keep) } : {}) };
        const ok = await saveDevice(keep, tune, extra);
        if (!ok) return;
        for (const x of ps) if (x.id !== keep.id) await api.del(`/api/sources/${x.id}`);
      }
      say(`Saved ${name}`); setOpen(null); load();
    } catch (e) { say(`Couldn't save: ${(e as Error).message}`); }
  };
  const remove = async (s: Source) => {
    if (s.group) await api.del(`/api/sources/group/${s.group}`); else await api.del(`/api/sources/${s.id}`);
    setConfirmRemove(null); setOpen(null); say(`Removed ${s.name}. Its past readings stay in your history.`); load();
  };
  const saveCal = async (c: Cal) => { setCal(c); try { await api.put("/api/calibration", c); } catch (e) { say(`Couldn't save: ${(e as Error).message}`); } };
  const tare = async (id: string) => { try { setCal(await api.post<Cal>(`/api/calibration/tare/${id}`)); say(`Zeroed. That side now reads 0 ${wu} when empty.`); } catch (e) { say((e as Error).message); } };

  // ---------- the list, by person ----------
  // every kind of device, by whose it is (deviceGroups is the one place that decides)
  const groups = deviceGroups(people, { sources, dials, phones, cloud });
  const reading = (s: Source) => {
    const ps = partsOf(s);
    if (ps.length > 1 || s.group) return ps.map(x => `${sideWord(x.zone ?? "left")}: ${fmt(x)}`).join(" · ");
    return fmt(s);
  };
  const forText = (s: Source) => { const o = ownerOf(s, people); return o === "both" ? (s.group ? "Both sides" : "Both") : people.find(p => p.id === o)?.name ?? ""; };
  const via = (s: Source) => s.driver === "ha-entity" ? "Home Assistant" : s.driver === "mqtt-topic" ? "MQTT" : s.driver === "ics" ? "Calendar link" : "Simulated";

  const editor = (s: Source) => {
    const et = typeOf(editMeta.kind); const eroles = et.roles ?? [et.role]; const erole = eroles.includes(editMeta.role) ? editMeta.role : eroles[0];
    const sided = isSided(et.id, erole);
    const tile = tileOf({ ...s, kind: et.id, role: erole });
    const ch = whoChoices(tile, false);
    const canSplit = sided && (s.driver === "ha-entity" || s.driver === "mqtt-topic" || !!s.group);
    const split = sided && editMeta.who === "both";
    const ps = partsOf(s);
    const two = s.params.layout === "two_sensors" && erole === "presence";      // two single-zone sensors, one per side
    return <div className="devtune" role="region" aria-label={`Edit ${s.name}`}>
      <div className="grid3">
        <label className="fld">Name<input type="text" value={editMeta.name} maxLength={80} onChange={e => setEditMeta(m => ({ ...m, name: e.target.value }))} /></label>
        <label className="fld">Who it's for<select value={editMeta.who} onChange={e => setEditMeta(m => ({ ...m, who: e.target.value }))}>
          {people.map(p => <option key={p.id} value={p.id}>{sided ? `${p.name.toLowerCase() === p.zone ? sideWord(p.zone) : `${p.name}'s`} side (${sideWord(p.zone).toLowerCase()})` : p.name}</option>)}
          {(ch.both && (!sided || canSplit)) && <option value="both">{sided ? "Both sides" : "Both of us"}</option>}</select></label>
        <label className="fld">What it is<select value={editMeta.kind} onChange={e => { const nt = typeOf(e.target.value); setEditMeta(m => ({ ...m, kind: nt.id, role: (nt.roles ?? [nt.role]).includes(m.role) ? m.role : (nt.roles ?? [nt.role])[0] }));
            setEdit(x => ({ ...Object.fromEntries(nt.tuning.map(f => [f.key, f.default])), ...Object.fromEntries(Object.entries(x).filter(([k]) => nt.tuning.some(f => f.key === k))) })); }}>
          {types.map(tt => <option key={tt.id} value={tt.id}>{tt.label}</option>)}</select></label>
        {eroles.length > 1 && <label className="fld">What it measures<select value={erole} onChange={e => setEditMeta(m => ({ ...m, role: e.target.value }))}>{eroles.map(r => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select></label>}
        <label className="fld">Model (optional)<input type="text" value={editMeta.device} maxLength={80} placeholder={et.examples.split(",")[0]} onChange={e => setEditMeta(m => ({ ...m, device: e.target.value }))} /></label>
      </div>
      {editMeta.who === "" && <small style={{ color: "var(--warn)" }}>Pick who it's for.</small>}

      {split ? <>
        <span className="k">{two ? "The sensor on each side" : "What watches each side"}</span>
        <BedZones people={people} active={zoneOn} onActive={setZoneOn} noun={et.id === "scale" ? "load cells" : two ? "sensor" : "zone"}
          parts={Object.fromEntries((["left", "right"] as const).map(z => { const live = sources.find(x => x.id === parts[z]?.id);
            return [z, { what: parts[z]?.name ?? parts[z]?.entity_id ?? parts[z]?.topic ?? null, reading: live ? `Now: ${fmt(live)}` : null }]; }))} />
        {s.driver === "ha-entity" && haShared && s.params.conn === "ha" && <HaPicker key={`${et.id}-${zoneOn}`} kind={two && et.id === "radar" ? "radar_single" : et.id} label={`${sideWord(zoneOn)} side`} value={parts[zoneOn]?.entity_id ?? ""}
          exclude={[parts[zoneOn === "left" ? "right" : "left"]?.entity_id ?? ""]}
          onPick={e => setParts(x => ({ ...x, [zoneOn]: e ? { ...x[zoneOn], entity_id: e.entity_id, name: e.name } : { id: x[zoneOn]?.id } }))} />}
        {s.driver === "ha-entity" && !(haShared && s.params.conn === "ha") && <label className="fld">{sideWord(zoneOn)} side entity ID<input type="text" value={parts[zoneOn]?.entity_id ?? ""} onChange={e => setParts(x => ({ ...x, [zoneOn]: { ...x[zoneOn], entity_id: e.target.value, name: e.target.value } }))} /></label>}
        {s.driver === "mqtt-topic" && <label className="fld">{sideWord(zoneOn)} side topic<input type="text" value={parts[zoneOn]?.topic ?? ""} onChange={e => setParts(x => ({ ...x, [zoneOn]: { ...x[zoneOn], topic: e.target.value, name: e.target.value } }))} /></label>}
        {parts[zoneOn]?.id && <button type="button" className="link" style={{ justifySelf: "start" }} onClick={() => setParts(x => ({ ...x, [zoneOn]: undefined }))}>Stop watching the {sideWord(zoneOn).toLowerCase()} side</button>}
      </> : connFields(s).length > 0 && ps.length === 1 && <>
        <span className="k">Connection</span>
        {s.driver === "ha-entity" && s.params.conn === "ha" && haShared && <HaPicker kind={editMeta.kind} value={editConn.entity_id ?? ""} onPick={e => setEditConn(x => ({ ...x, entity_id: e?.entity_id ?? x.entity_id }))} />}
        <details className="conn-details"><summary className="mute">{s.driver === "ics" ? `Link: ${s.params.url ?? "—"}` : "Details"}</summary>
          <div className="grid3">{connFields(s).filter(([k]) => !(k === "entity_id" && s.driver === "ha-entity" && s.params.conn === "ha" && haShared)).map(([k, label, ph]) => <label key={k} className="fld" style={s.driver === "ics" ? { gridColumn: "1 / -1" } : undefined}>{label}
            <input type="text" autoComplete="off" spellCheck={false} value={editConn[k] ?? ""} placeholder={ph} onChange={e => setEditConn(x => ({ ...x, [k]: e.target.value }))} /></label>)}</div>
          {s.driver === "ha-entity" && (s.params.conn === "ha" ? <small className="faint">Uses the shared Home Assistant connection (Integrations → Home Assistant).</small>
            : <span className="faint" style={{ fontSize: 12.5 }}>Has its own Home Assistant address and token.{haShared && <> <button type="button" className="link" onClick={async () => { if (await saveDevice(s, edit, { params: connParams(s, true) })) say(`${s.name} now uses the shared connection`); }}>Use the shared connection instead</button></>}</span>)}
        </details>
      </>}

      {s.votes && <span className="faint" style={{ fontSize: 12.5 }}>{ps.filter(x => x.zone).map(x => `${sideWord(x.zone!)}: ${x.vote == null ? "nothing to say right now" : `${x.vote ? "says in bed" : "says empty"}, about ${share(x.zone!, x.vote_weight ?? 0)}% of that side's vote`}`).join(" · ")}<Tip text={VOTE_TIP} label="How the in-bed vote works" /></span>}
      <Fold id="devices.tune.about" className="fold-sub" title="What it's for and how to check it"><Purpose kind={et.id} /><HowTo kind={et.id} /></Fold>
      <Fold id="devices.tune.read" className="fold-sub" title={`How it's read${["presence", "light", "home"].includes(erole) ? " (what on and off mean)" : ""}`} summary="recommended values unless you changed them">
        {(() => { const mapped = et.tuning.some(f => f.key === "value_map") && ["presence", "light", "home"].includes(erole);
          const raw = s.raw_seen?.length ? s.raw_seen[s.raw_seen.length - 1].value : null;
          return <>
            {!mapped && <small className="faint">Right now: <b>{s.value == null ? "no reading" : `${Math.round(s.value * 100) / 100}${s.unit ? ` ${s.unit}` : ""}`}</b>{raw != null && String(s.value) !== raw ? ` (it sent "${raw}")` : ""}</small>}
            {mapped && <ValueMap role={erole} live={raw} seen={s.raw_seen ?? []} values={edit} set={(k, v) => setEdit(x => ({ ...x, [k]: v }))} />}
            <TuningFields weightUnit={wu} fields={et.tuning} values={edit} role={erole} mapped={mapped} set={(k, v) => setEdit(x => ({ ...x, [k]: v }))} />
          </>; })()}
        <button type="button" className="pill" style={{ justifySelf: "start" }} onClick={() => setEdit(Object.fromEntries(et.tuning.map(f => [f.key, f.default])))}>Back to recommended</button>
      </Fold>
      <div className="pills devtune-actions">
        {s.kind === "scale" && ps.map(x => confirmZero === x.id
          ? <span key={x.id} className="pills"><button type="button" className="pill" style={{ borderColor: "var(--warn)", color: "var(--warn)" }} onClick={() => { setConfirmZero(null); tare(x.id); }}>{x.zone ? `${sideWord(x.zone)} side` : "Side"} is empty: zero it</button>
              <button type="button" className="pill" onClick={() => setConfirmZero(null)}>Cancel</button></span>
          : <button type="button" key={x.id} className="pill" title="Do this with that side empty" onClick={() => {
              if (x.vote || (x.value ?? 0) >= Number(x.tuning_resolved.threshold_kg ?? 20)) { say(`${x.name} reads ${weight(x.value ?? 0, wu)}, so someone seems to be on that side. Zero it when the side is empty.`); return; }
              setConfirmZero(x.id); }}>Zero{ps.length > 1 && x.zone ? ` ${sideWord(x.zone).toLowerCase()}` : ""}</button>)}
        <button type="button" className="pill" onClick={async () => { for (const x of ps) await saveDevice(x, x.tuning, { enabled: !s.enabled }); }}>{s.enabled ? "Pause" : "Resume"}</button>
        {confirmRemove === s.id
          ? <><button type="button" className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={() => remove(s)}>Remove{ps.length > 1 ? " both sides" : ""}?</button>
              <button type="button" className="pill" onClick={() => setConfirmRemove(null)}>Keep</button></>
          : <button type="button" className="pill" onClick={() => setConfirmRemove(s.id)}>Remove</button>}
        <span className="devtune-save"><button type="button" className="btn" onClick={() => setOpen(null)}>Cancel</button>
          <button type="button" className="btn pri" disabled={!editMeta.who} onClick={() => void saveEdit(s)}>Save</button></span>
      </div>
      <small className="faint">Its past readings are kept whatever you change here.</small>
    </div>;
  };

  const row = (s: Source) => {
    const ps = partsOf(s);
    const issue = ps.find(x => x.enabled && x.health && !["ok", "error"].includes(x.health.status));
    const err = ps.find(x => x.enabled && x.error);
    return <div key={s.id} id={`dev-${s.id}`} className={`devline${open === s.id ? " editing" : ""}`} style={{ opacity: s.enabled ? 1 : 0.6 }}>
      <div className="devline-main">
        <span className={`sdot ${dot(ps)}`} aria-label={dot(ps) === "ok" ? "Working" : dot(ps) === "off" ? (s.enabled ? "Waiting" : "Paused") : "Needs a look"} role="img" />
        <span className="devline-id"><b>{s.name}</b><small className="faint">{s.kind === "custom" ? ROLE_LABEL[s.role] : s.kind_label}{s.params.layout === "two_sensors" ? " · two sensors, one per side" : ""} · {via(s)}{s.device && !/simulated/i.test(s.device) ? ` · ${s.device}` : ""}</small>
          {issue && <span className="devline-issue"><Tip text={HEALTH_TIP[issue.health!.status]}><span className={`health-badge${issue.health!.status === "erratic" ? " is-erratic" : ""}`}>
            {{ erratic: issue.health!.trusted ? "Acting up · trusted anyway" : "Set aside", offline: "Offline", stale: "No usable reading", paused: "Paused" }[issue.health!.status as "erratic" | "offline" | "stale" | "paused"]}</span></Tip>
            <small className="faint"> {issue.health!.why}</small>
            {issue.health!.status === "erratic" && !issue.health!.trusted && <button type="button" className="link" onClick={() => saveDevice(issue, { ...issue.tuning_resolved, ignore_health: true }).then(ok => ok && say(`${issue.name} will be used even when it acts up.`))}>Trust it anyway</button>}</span>}
          {err && <small style={{ color: "var(--bad)" }}>{err.error}</small>}</span>
        <span className="devline-read"><span>{reading(s)}{s.enabled && s.tuning_resolved.invert && ["presence", "light", "home"].includes(s.role) ? <Tip text={REVERSED_TIP} label="What does reversed mean?" /> : null}</span>
          <small className="mute">{!s.enabled ? "paused" : ago(s)}</small></span>
        <span className="devline-who tag">{forText(s)}</span>
        <span className="devline-edit devline-acts">
          {confirmRemove === `row:${s.id}`
            ? <><button type="button" className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={() => remove(s)}>Remove {s.name}{ps.length > 1 ? " (both sides)" : ""}?</button>
                <button type="button" className="pill" onClick={() => setConfirmRemove(null)}>Keep</button></>
            : <>{open !== s.id && <button type="button" className="pill" aria-label={`Remove ${s.name}`} onClick={() => setConfirmRemove(`row:${s.id}`)}>Remove</button>}
                <button type="button" className="pill" aria-expanded={open === s.id} onClick={() => openEdit(s)}>{open === s.id ? "Close" : "Edit"}</button></>}
        </span>
      </div>
      {open === s.id && editor(s)}
    </div>;
  };

  const strip = (p: PersonView) => <div key={`strip-${p.zone}`} className="devline">
    <div className="devline-main">
      <span className={`sdot ${snap.system.online ? "ok" : "bad"}`} role="img" aria-label={snap.system.online ? "Working" : "Offline"} />
      <span className="devline-id"><b>Bed sensor strip · {sideWord(p.zone).toLowerCase()}</b><small className="faint">{snap.system.name}{sim ? " · simulated" : ""} · in bed, heart rate and breathing</small></span>
      <span className="devline-read"><span>{snap.system.online ? (p.presence.bed_present ? "someone there" : "empty") : "offline"}</span>
        <small className="mute">{snap.system.online ? `about ${share(p.zone, cal.weights.bed ?? 0.5)}% of the in-bed vote` : "the bed isn't reporting"}</small></span>
      <span className="devline-who tag">{p.name}</span>
      <span className="devline-edit" />
    </div></div>;

  return (
    <>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div><div className="k">Configure</div><h1 style={{ fontSize: 32 }}>Devices &amp; calibration</h1>
          <p className="mute" style={{ fontSize: 13 }}>What tells Ember &amp; Tide when each of you is in bed, how you sleep, and what the room is doing. The bed's own strip is always there; the rest is optional. <HelpLink to="devices" label="How devices work" /></p></div>
        {!adding ? <button type="button" className="btn pri" onClick={() => setAdding({ start: null })}>+ Add a device</button> : <button type="button" className="btn" onClick={() => setAdding(null)}>Cancel</button>}
      </div>

      {adding && <AddDevice key={adding.start ?? "pick"} start={adding.start} people={people} types={types} sources={sources} sim={sim} say={say}
        onDone={() => { setAdding(null); load(); }} onCancel={() => setAdding(null)} />}

      {!adding && <DeviceCatalog sources={sources} people={people} dials={dials}
        onAdd={t => { setAdding({ start: t }); window.scrollTo({ top: 0, behavior: "smooth" }); }}
        onEdit={editById} />}

      {!adding && <Calibration people={people} units={units} weightUnit={wu} cal={cal} scales={sources.filter(s => s.kind === "scale")} onEdit={editById} />}

      {!adding && recs.length > 0 && <Fold id="devices.recs" className="box recbox" title="Recommended next" keepSummary summary={`${recs.length} suggestion${recs.length === 1 ? "" : "s"}`}>
        <ul>{recs.map(r => <li key={r}>{r}</li>)}</ul></Fold>}

      {groups.map(({ key, label, person: p, items }) => {
        const n = items.length + (p ? 1 : 0);
        if (!n) return null;
        const bad = items.filter(it => it.type === "source" && partsOf(it.s as Source).some(needsAttention)).length;
        const editDials = () => { setAdding({ start: "dials" }); window.scrollTo({ top: 0, behavior: "smooth" }); };
        const holdsOpen = !!open && items.some(it => it.type === "source" && partsOf(it.s as Source).some(x => x.id === open));
        return <Fold key={key} id={`devices.who.${key}`} className="box devgroup" title={label} attention={bad > 0} reveal={holdsOpen} keepSummary defaultOpen
          summary={`${n} device${n === 1 ? "" : "s"} · ${bad ? `${bad} need${bad === 1 ? "s" : ""} a look` : "all fine"}`}>
          {p && <span className="devgroup-colour" style={{ background: personaColor(p) }} aria-hidden />}
          {items.map(it => it.type === "source" ? row(it.s as Source)
            : it.type === "dial" ? <DialLine key={it.key} d={it.d} people={people} say={say} onChanged={loadExtras} onEdit={editDials} />
            : it.type === "phone" ? <PhoneLine key={it.key} r={it.r} snap={snap} me={me?.id ?? null} say={say} onChanged={loadExtras} />
            : it.type === "cloud" ? <CloudLine key={it.key} mine={!auth.state.enabled || auth.me?.person === it.person} provider={it.provider} person={people.find(x => x.id === it.person)!} say={say} onChanged={loadExtras} />
            : <WearableLine key={it.key} brand={it.brand} person={people.find(x => x.id === it.person)!} say={say} />)}
          {p && strip(p)}
          {!items.length && p && <span className="faint" style={{ fontSize: 12.5 }}>Only the bed strip so far. <button type="button" className="link" onClick={() => setAdding({ start: "wearable" })}>Add {p.name}'s watch or ring</button></span>}
        </Fold>;
      })}

      <div className="grid2 even devgrid">
        <Fold id="devices.votes" className="box" title="In-bed vote · how much each signal counts" keepSummary
          summary={(["left", "right"] as const).map(z => { const fz = side(z)?.presence.fusion; return `${sideWord(z)} ${fz?.present == null ? "unknown" : fz.present ? "in bed" : "empty"}`; }).join(" · ")}>
          <p className="faint" style={{ fontSize: 12 }}>Turn a device up if it's right more often than the others, down if it misfires. Recommended: bed strip 0.50, radar 0.35, bed scale 0.60.
            <Tip label="How the weighted vote works" text="Each side counts as in bed when the weighted vote reaches 50% (the line on the bar). Only that side's devices take part; one with nothing to say is left out, and the others share its part." /></p>
          {(["left", "right"] as const).map(z => {
            const voters = sources.filter(s => s.zone === z && s.votes && s.enabled);
            const p = side(z); const fz = p?.presence.fusion;
            return <div key={z} style={{ display: "grid", gap: 6, padding: "8px 0", borderTop: "1px solid var(--line)" }}>
              <div className="row"><b style={{ fontWeight: 500, color: p ? personaColor(p) : undefined }}>{sideWord(z)}{p && p.name.toLowerCase() !== z ? ` · ${p.name}` : ""}</b>
                <span>{fz?.present == null ? "unknown" : fz.present ? "in bed" : "empty"} {fz?.score != null && <span className="mute num">({Math.round(fz.score * 100)}%)</span>}</span></div>
              <div style={{ height: 6, borderRadius: 3, background: "var(--raise)", position: "relative" }}><div style={{ position: "absolute", inset: 0, width: `${(fz?.score ?? 0) * 100}%`, background: "var(--acc)", borderRadius: 3 }} />
                <div style={{ position: "absolute", left: "50%", top: -3, bottom: -3, width: 1, background: "var(--text)", opacity: 0.4 }} /></div>
              <label className="voterow"><span>Bed sensor strip <small className="faint">(both sides share this weight)</small></span>
                <input type="range" min={0} max={1} step={0.05} value={cal.weights.bed ?? 0.5} onChange={e => setCal({ ...cal, weights: { ...cal.weights, bed: Number(e.target.value) } })} onMouseUp={() => saveCal(cal)} onTouchEnd={() => saveCal(cal)} onKeyUp={() => saveCal(cal)} />
                <span className="num">{(cal.weights.bed ?? 0.5).toFixed(2)}</span></label>
              {voters.map(s => <label key={s.id} className="voterow"><span>{s.name} <small className="faint">{s.kind_label}</small> <button type="button" className="link" onClick={e => { e.preventDefault(); openEdit(s, true); }}>Edit</button></span>
                <input type="range" min={0} max={1} step={0.05} value={s.vote_weight ?? 0.3}
                  onChange={e => setSources(xs => xs.map(x => x.id === s.id ? { ...x, vote_weight: Number(e.target.value) } : x))}
                  onMouseUp={e => saveDevice(s, { ...s.tuning, weight: Number((e.target as HTMLInputElement).value) })}
                  onTouchEnd={e => saveDevice(s, { ...s.tuning, weight: Number((e.target as HTMLInputElement).value) })}
                  onKeyUp={e => saveDevice(s, { ...s.tuning, weight: Number((e.target as HTMLInputElement).value) })} />
                <span className="num">{(s.vote_weight ?? 0.3).toFixed(2)}</span></label>)}
              {!voters.length && <span className="faint" style={{ fontSize: 12 }}>Only the bed strip votes on this side.</span>}
            </div>;
          })}
          <span className="k" style={{ marginTop: 8 }}>Body weight<Tip label="Why body weight?" text="Only matters with a bed scale: it tells the two of you (and a pet) apart. Weight imported from Health Connect fills this in by itself." /></span>
          {people.map(p => <label key={p.id} className="voterow" style={{ gridTemplateColumns: "1fr auto" }}>
            <span>{p.name}</span><span><input type="number" style={{ width: 80 }} aria-label={`${p.name}'s body weight in ${wu}`}
              value={cal.person_kg[p.id] == null ? "" : wu === "kg" ? cal.person_kg[p.id] : Math.round(kgTo(cal.person_kg[p.id], "lb"))} placeholder="—"
              onChange={e => setCal({ ...cal, person_kg: { ...cal.person_kg, [p.id]: Math.round(toKg(Number(e.target.value), wu) * 100) / 100 } })} onBlur={() => saveCal(cal)} /> {wu}</span></label>)}
        </Fold>
        <Fold id="devices.agree" className="box" title="Agreement with the combined answer · this night" keepSummary summary={people.map(p => `${p.name}: ${(agree[p.id] ?? []).length ? `${(agree[p.id] ?? []).length} signals` : "needs a night"}`).join(" · ")}>
          <p className="faint" style={{ fontSize: 12 }}>How often each device matched the side's final in-bed answer.<Tip label="What to do about a low score" text="A device that often disagrees is either placed badly or needs tuning. For a radar, a longer hold usually fixes it." /></p>
          {people.map(p => (
            <div key={p.id} style={{ display: "grid", gap: 4 }}>
              <span style={{ fontSize: 13 }}>{p.name}</span>
              {(agree[p.id] ?? []).map(a => { const sid = a.signal.split(":")[1]; const nm = a.signal === "bed" ? "Bed sensor strip" : sources.find(s => s.id === sid)?.name ?? a.signal;
                return <div key={a.signal} className="agreerow">
                  <span className="mute">{nm}</span><div style={{ height: 6, borderRadius: 3, background: "var(--raise)" }}><div style={{ height: 6, width: `${a.agree * 100}%`, borderRadius: 3, background: a.agree > 0.9 ? "var(--good)" : a.agree > 0.75 ? "var(--warn)" : "var(--bad)" }} /></div>
                  <span className="num">{Math.round(a.agree * 100)}%</span></div>; })}
              {!(agree[p.id] ?? []).length && <span className="faint" style={{ fontSize: 12 }}>Needs a night of data.</span>}
            </div>
          ))}
        </Fold>
      </div>
    </>
  );
}

