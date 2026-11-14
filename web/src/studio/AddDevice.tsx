import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import Fold from "../components/Fold";
import Tip from "../components/Tip";
import { useApp } from "../ctx";
import { toKg, weightUnit } from "../format";
import type { PersonView } from "../types";
import { BRANDS, type Brand, gives } from "../wearables";
import BedZones from "./BedZones";
import BedSidePicker, { peopleHalves, type Side } from "../components/BedSidePicker";
import { type DevSource, Ico, type PathId, type TileId, TILES, isSided, ownerOf, personLabel, sideWord, tileOf, whoChoices, whoText } from "./deviceKinds";
import { type DevType, Purpose, ROLE_LABEL, TuningFields } from "./deviceShared";
import DialSetup from "./DialSetup";
import HaPicker, { type HaEnt } from "./HaPicker";

/**
 * The one way to add a device: what it is → how it connects → who it's for → set it up (connection and name) → done.
 * The "+ Add a device" button starts at the first step, a catalog tile at the second. Paths that need nothing
 * here (Health Connect on the phone, the Android app, Oura's cloud) end with plain instructions instead of a form.
 */

interface PathInfo { id: PathId; title: string; text: string; soon?: string | false; best?: boolean }
type Step = "kind" | "brand" | "path" | "who" | "setup";
const GUIDE: PathId[] = ["hc", "ah", "cloud", "app", "webhook"];
const SIM_HELP: Record<string, [string, string, string]> = {
  phone: ["Away from", "Back home at", "Leave both empty and the phone stays home."],
  location: ["Leaves home at", "Gets home at", "Drives out and back at about 45 km/h. Leave empty to stay home."],
  calendar: ["Event starts", "Event ends", "One evening event. Leave empty for a free evening."],
  light: ["Light off at", "Light on at", "Bedroom light goes off at bedtime and back on in the morning."],
};
const OTHER_BRAND = "Something else / add my own";
const LAYOUTS: { id: "zones" | "two_sensors" | "one"; title: string; text: string; note?: string }[] = [
  { id: "zones", title: "One radar with zones for both sides", text: "An Aqara FP2, LD2450 board or Everything Presence that maps a zone to each side." },
  { id: "two_sensors", title: "Two single-zone sensors, one per side", text: "For example two Aqara FP1E on Zigbee, one by each nightstand.",
    note: "Place each low by its nightstand, aimed across its own side; a little crosstalk is expected and handled." },
  { id: "one", title: "One side only", text: "A single sensor that watches one side of the bed." },
];

export default function AddDevice({ start, people, types, sources, sim, say, onDone, onCancel }: {
  start: TileId | null; people: PersonView[]; types: DevType[]; sources: DevSource[]; sim: boolean;
  say: (m: string) => void; onDone: () => void; onCancel: () => void;
}) {
  const { me } = useApp();
  const wu = weightUnit(me?.settings ?? people[0]?.settings);       // lb or kg, as the person looking picked
  const [tile, setTile] = useState<TileId | null>(start);
  const [step, setStep] = useState<Step>(start ? (start === "wearable" ? "brand" : "path") : "kind");
  const [brand, setBrand] = useState<Brand | null>(null);
  const [path, setPath] = useState<PathId | null>(null);
  const [who, setWho] = useState<string>("");
  const [kindSel, setKindSel] = useState<string>("");
  const [role, setRole] = useState<string>("");
  const [ent, setEnt] = useState<Partial<Record<"one" | "left" | "right", HaEnt>>>({});
  const [zoneOn, setZoneOn] = useState<"left" | "right">("left");
  // presence for both sides: one radar with a zone per side, or two single-zone sensors (one by each nightstand)
  const [layout, setLayout] = useState<"zones" | "two_sensors" | "one" | "">("");
  const [conn, setConn] = useState({ url: "", topic: "", topicL: "", topicR: "", json: "", ics: "", a: "", b: "", km: "15", title: "", kg: wu === "lb" ? "165" : "75" });
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [tuning, setTuning] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [haShared, setHaShared] = useState<boolean | null>(null);
  const [oura, setOura] = useState<{ configured?: boolean; people?: Record<string, boolean> } | "absent" | null>(null);
  const [cloud, setCloud] = useState<boolean | null>(null);
  useEffect(() => {
    api.get<{ configured: boolean }>("/api/ha/connection").then(c => setHaShared(c.configured)).catch(() => setHaShared(false));
    api.get<{ configured?: boolean; people?: Record<string, boolean> }>("/api/oura/status").then(setOura).catch(() => setOura("absent"));
    api.get<unknown>("/api/cloud/providers").then(() => setCloud(true)).catch(() => setCloud(false));   // helper framework, when present
  }, []);
  useEffect(() => { document.querySelector(".addflow")?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [step]);

  const t = tile ? TILES.find(x => x.id === tile)! : null;
  const guide = !!path && GUIDE.includes(path);
  const choice = tile ? whoChoices(tile, guide) : null;
  // the server type this becomes
  const kind = !tile ? "custom" : tile === "presence" ? (kindSel || "radar") : tile === "phone" ? (kindSel || "phone")
    : tile === "wearable" ? "wearable" : tile === "other" ? "custom" : tile;
  const type = types.find(x => x.id === kind) ?? types.find(x => x.id === "custom");
  const roles = type?.roles ?? (type ? [type.role] : []);
  const r = roles.includes(role) ? role : roles[0] ?? "other";
  const sided = isSided(kind, r);
  const split = sided && who === "both";
  const two = split && tile === "presence" && layout === "two_sensors";
  const pickerKind = two && kind === "radar" ? "radar_single" : kind;      // two FP1E-style sensors: on/off occupancy only
  const person = people.find(p => p.id === who);
  const skipWho = tile === "room" || tile === "light" || tile === "leak";

  const paths: PathInfo[] = useMemo(() => {
    if (!tile) return [];
    const ha: PathInfo = { id: "ha", title: "Home Assistant", text: haShared === false ? "Pick it from your Home Assistant entities (connect Home Assistant first, once)." : "Pick it from your Home Assistant entities, filtered to what fits." };
    const mqtt: PathInfo = { id: "mqtt", title: "MQTT topic (advanced)", text: "ESPHome, Zigbee2MQTT or your own publisher, straight from the broker." };
    const simP: PathInfo = { id: "sim", title: "Simulated", text: "This is a test server: a pretend device for trying things out." };
    const list: PathInfo[] = [];
    if (tile === "wearable") {
      const b = brand;
      const other = !b || b.id === "other";
      if (other || b.hc) list.push({ id: "hc", title: "Android phone → Health Connect", text: other ? "If its app syncs to Health Connect, Ember & Tide's Android app reads it every few hours." : `${b.name} usually gives ${gives(b.hc) || "some data"}. Ember & Tide's Android app reads it every few hours.` });
      if (other || b.ah) list.push({ id: "ah", title: "iPhone → Apple Health", text: other ? "Through Apple Health, in the iPhone app." : `${b.name} usually gives ${gives(b.ah) || "some data"} through Apple Health.`, soon: "Coming with the iPhone app" });
      if (b?.cloud) list.push({ id: "cloud", title: `Straight from ${b.name.replace(" ring", "")}'s cloud`, text: `Works with any phone (also an iPhone using the web app): ${gives(b.cloud)}, a few times a day.`, soon: oura === "absent" ? "Coming in an update" : false });
      list.push({ ...ha, text: "For a watch already in Home Assistant (for example heart rate from the companion app): live readings during the night." });
      if (other) list.push({ id: "webhook", title: "Web hook or JSON address", text: "For a service that can send readings to a web address, or has one Ember & Tide can read.", soon: cloud ? false : "Coming in an update" });
    } else if (tile === "phone") {
      list.push({ id: "app", title: "The Android app", text: "Settings → From this phone: home or away, next alarm, charging at the bedside. Nothing to wire up." });
      list.push({ id: "iosapp", title: "The iPhone app", text: "Home or away, next alarm and charging will come with the iPhone app. Until then, use Home Assistant's companion app (below).", soon: "Coming soon" });
      list.push({ ...ha, text: "A phone tracker you already have in Home Assistant (router, UniFi, companion app) or its distance from home." });
    } else if (tile === "calendar") {
      list.push({ id: "ics", title: "Calendar link", text: "A private calendar address from Fastmail, iCloud or Google. Read only." });
      list.push({ ...ha, text: "A calendar entity you already have in Home Assistant." });
    } else {
      list.push(ha, mqtt);
      if (tile === "other") list.push({ id: "webhook", title: "Web hook or JSON address", text: "A service that sends readings to a web address, or has one Ember & Tide can read.", soon: cloud ? false : "Coming in an update" });
    }
    if (sim) list.push(simP);
    const first = list.find(p => !p.soon);
    return list.map(p => ({ ...p, best: p === first && list.length > 1 }));
  }, [tile, brand, haShared, oura, cloud, sim]);

  const steps: Step[] = [...(start ? [] : ["kind" as Step]), ...(tile === "wearable" ? ["brand" as Step] : []), "path", ...(skipWho ? [] : ["who" as Step]), "setup"];
  const STEP_NAME: Record<Step, string> = { kind: "What is it?", brand: "Which one?", path: "How it connects", who: "Who it's for", setup: guide ? "What to do" : "Set it up" };
  const back = () => { setErr(null); const i = steps.indexOf(step); if (i <= 0) onCancel(); else setStep(steps[i - 1]); };
  const next = (s: Step) => { setErr(null); const i = steps.indexOf(s); setStep(steps[i + 1] ?? "setup"); };
  const pickTile = (id: TileId) => { setTile(id); setBrand(null); setPath(null); setWho(""); setLayout(""); setKindSel(""); setEnt({}); setStep(id === "wearable" ? "brand" : "path"); };
  const pickPath = (p: PathId) => {
    setPath(p); setEnt({});
    const c = tile ? whoChoices(tile, GUIDE.includes(p)) : null;
    setWho(c?.default === "both" && c.both ? "both" : people.length === 1 ? people[0].id : "");
    setLayout("");
    setStep(skipWho ? "setup" : "who");
  };
  const pickWho = (w: string, lay: typeof layout = layout) => {
    setWho(w); setLayout(lay);
    const base = brand && brand.id !== "other" ? brand.name : lay === "two_sensors" ? "Nightstand sensors" : t?.title ?? "Device";
    const owner = people.find(p => p.id === w);
    setName(owner && people.length > 1 ? `${base} · ${owner.name}` : base);
    setModel(brand && brand.id !== "other" ? brand.name : "");
    setStep("setup");
  };
  useEffect(() => { if (step === "setup" && skipWho && !name) setName(t?.title ?? "Device"); }, [step]);

  // ---------- add ----------
  const simParams = (zoneRole: string): Record<string, unknown> => {
    const p: Record<string, unknown> = { kind: zoneRole };
    if (kind === "scale") p.kg = Math.round(toKg(Number(conn.kg), wu) * 10) / 10 || 75;
    if (conn.a && conn.b) Object.assign(p, kind === "phone" ? { away: [[conn.a, conn.b]] } : kind === "location" ? { trip: { leave: conn.a, arrive: conn.b, km: Number(conn.km) || 15, speed: 45 } }
      : kind === "calendar" ? { events: [[conn.a, conn.b, conn.title || "Evening event"]] } : kind === "light" ? { off: conn.a, on: conn.b } : {});
    return p;
  };
  const partParams = (z: "one" | "left" | "right"): Record<string, unknown> | null => {
    if (path === "ha") return ent[z] ? { conn: "ha", entity_id: ent[z]!.entity_id } : null;
    if (path === "mqtt") { const topic = z === "left" ? conn.topicL : z === "right" ? conn.topicR : conn.topic; return conn.url && topic ? { url: conn.url.trim(), topic: topic.trim(), ...(conn.json ? { json_path: conn.json.trim() } : {}) } : null; }
    if (path === "ics") return /^(https?|webcal):\/\//i.test(conn.ics.trim()) ? { url: conn.ics.trim() } : null;
    if (path === "sim") return simParams(r);
    return null;
  };
  const ready = !!name.trim() && (split ? !!(partParams("left") || partParams("right")) : !!partParams("one")) && (skipWho || !!who);
  const driver = path === "ha" ? "ha-entity" : path === "mqtt" ? "mqtt-topic" : path === "ics" ? "ics" : "sim";
  const add = async () => {
    setBusy(true); setErr(null);
    try {
      if (split) {
        const parts = (["left", "right"] as const).map(z => ({ zone: z, params: partParams(z) })).filter(p => p.params);
        await api.post("/api/sources/group", { name: name.trim(), kind, role: r, driver, device: model.trim() || null, tuning, parts,
          ...(tile === "presence" ? { layout: two ? "two_sensors" : "zones" } : {}) });
      } else {
        const personal = ["wearable", "phone", "location", "calendar"].includes(kind) || kind === "custom";
        await api.post("/api/sources", { name: name.trim(), driver, role: r, kind, zone: sided ? person?.zone ?? null : null,
          person: personal && who !== "both" ? who || null : null, device: model.trim() || null, params: partParams("one"), tuning });
      }
      say(`${name.trim()} added`); onDone();
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  const onEnt = (z: "one" | "left" | "right", e: HaEnt | null) => {
    setEnt(x => ({ ...x, [z]: e ?? undefined }));
    if (e && roles.includes(e.guess_role)) setRole(e.guess_role);
    if (e && e.domain === "binary_sensor" && /empty|vacant|unoccupied|\bclear\b|_clear|no_?one/i.test(`${e.entity_id} ${e.name}`)) setTuning(x => ({ ...x, invert: true }));
    if (e && z === "left" && !ent.right) setZoneOn("right");
  };

  // ---------- screens ----------
  const head = <div className="addflow-head">
    <ol className="steps3" aria-label="Steps">{steps.map((s, i) => <li key={s} className={s === step ? "on" : steps.indexOf(step) > i ? "done" : ""} aria-current={s === step ? "step" : undefined}>{i + 1}. {STEP_NAME[s]}</li>)}</ol>
    {t && step !== "kind" && <span className="addflow-what"><Ico name={t.icon} size={20} />{brand && brand.id !== "other" ? brand.name : t.title}{path && step !== "path" ? ` · ${paths.find(p => p.id === path)?.title ?? ""}` : ""}{who && step === "setup" ? (two ? " · two sensors, one per side" : ` · for ${who === "both" ? (sided ? "both sides" : "both of you") : person?.name}`) : ""}</span>}
  </div>;
  const nav = (primary?: ReactNode) => <div className="pills addflow-nav"><button type="button" className="pill" onClick={back}>Back</button>{primary}</div>;

  if (tile === "dials") return <div className="box addflow"><DialSetup people={people} onClose={onDone} onBack={start ? onCancel : () => { setTile(null); setStep("kind"); }} /></div>;

  return <div className="box addflow" aria-live="polite">
    {head}

    {step === "kind" && <><h2 className="addflow-q">What would you like to add?</h2>
      <div className="catgrid">{TILES.map(x => <button type="button" key={x.id} className="cattile cat-pick" onClick={() => pickTile(x.id)}>
        <span className="cat-main"><Ico name={x.icon} /><span className="cat-text"><b>{x.title}</b><span>{x.adds}</span></span></span></button>)}</div>
      {nav()}</>}

    {step === "brand" && <><h2 className="addflow-q">Which watch or ring?</h2>
      <div className="choicegrid">{BRANDS.map(b => <button type="button" key={b.id} className="choice" onClick={() => { setBrand(b); next("brand"); }}>
        <span className="choice-ico" aria-hidden>{b.icon}</span><span className="choice-text"><b>{b.id === "other" ? OTHER_BRAND : b.name}</b>
          <span>{b.id === "other" ? "Any other watch, ring or band" : [b.hc && "Android", b.ah && "iPhone", b.cloud && "direct"].filter(Boolean).join(" · ")}</span></span></button>)}</div>
      {nav()}</>}

    {step === "path" && t && <><h2 className="addflow-q">How does {brand && brand.id !== "other" ? `your ${brand.name}` : `your ${t.title.toLowerCase()}`} connect?</h2>
      {tile === "wearable" && <p className="faint addflow-sub">Best first. A phone's health app is the easiest: nothing to wire up.</p>}
      <div className="choicegrid one">{paths.map(p => <button type="button" key={p.id} className="choice" disabled={!!p.soon} onClick={() => pickPath(p.id)}>
        <span className="choice-text"><b>{p.title}{p.best && <span className="tag good">Best</span>}{p.soon && <span className="tag">{p.soon}</span>}</b><span>{p.text}</span></span>
        <span className="choice-go" aria-hidden>›</span></button>)}</div>
      {(() => { const have = sources.filter(x => tileOf(x) === tile && (!x.group || sources.find(y => y.group === x.group) === x));
        return have.length > 0 && <p className="faint addflow-sub">Already added: {have.map(x => `${x.name} (${whoText(ownerOf(x, people), people)})`).join(", ")}. This adds another.</p>; })()}
      {tile === "presence" && <p className="faint addflow-sub">Aqara FP2, Everything Presence, LD2410 / LD2450 boards and pressure pads all show up in Home Assistant once added there.</p>}
      {nav()}</>}

    {step === "who" && choice && tile === "presence" && <><h2 className="addflow-q">How will it watch the bed?</h2>
      <div className="choicegrid one" role="group" aria-label="How it watches the bed">
        {LAYOUTS.map(l => <button type="button" key={l.id} className={`choice${layout === l.id ? " on" : ""}`} aria-pressed={layout === l.id}
          onClick={() => { if (l.id !== "one") pickWho("both", l.id); else if (people.length === 1) pickWho(people[0].id, "one"); else { setLayout("one"); setWho(""); } }}>
          <span className="choice-text"><b>{l.title}{l.id === "zones" && <span className="tag good">Usual</span>}</b><span>{l.text}</span>
            {l.note && <span className="choice-note">{l.note}</span>}</span>
          <span className="choice-go" aria-hidden>›</span></button>)}
      </div>
      {layout === "one" && people.length > 1 && <><h3 className="addflow-q2">Which side does it watch?</h3>
        <BedSidePicker value={(people.find(p => p.id === who)?.zone as Side | undefined) ?? null} halves={peopleHalves(people)} label="Which side it watches"
          caption="As you lie in bed, which side does it watch? Tap it." onChange={z => { const p = people.find(x => x.zone === z); if (p) pickWho(p.id, "one"); }} /></>}
      {nav()}</>}

    {step === "who" && choice && tile !== "presence" && <><h2 className="addflow-q">{tile === "wearable" || tile === "phone" ? `Whose ${tile === "phone" ? "phone" : "watch or ring"} is it?` : sided ? "Which side does it watch?" : "Who is it for?"}</h2>
      {sided && <BedSidePicker value={(people.find(p => p.id === who)?.zone as Side | undefined) ?? null} halves={peopleHalves(people)} label="Which side it watches"
        caption="As you lie in bed, which side does it watch? Tap it." onChange={z => { const p = people.find(x => x.zone === z); if (p) pickWho(p.id); }} />}
      <div className="choicegrid">
        {!sided && people.map(p => <button type="button" key={p.id} className={`choice${who === p.id ? " on" : ""}`} onClick={() => pickWho(p.id)}>
          <span className="choice-text"><b>{p.name}</b><span>{personLabel(p)}</span></span></button>)}
        {choice.both && <button type="button" className={`choice${who === "both" ? " on" : ""}`} onClick={() => pickWho("both")}>
          <span className="choice-text"><b>{sided ? "Both sides" : "Both of us"}{choice.default === "both" && <span className="tag good">Usual</span>}</b><span>{choice.both}</span></span></button>}
      </div>
      {nav()}</>}

    {step === "setup" && guide && <GuideStep path={path!} brand={brand} person={person} oura={oura} say={say} />}
    {step === "setup" && guide && nav(<button type="button" className="btn pri" onClick={onDone}>Done</button>)}

    {step === "setup" && !guide && type && <>
      <h2 className="addflow-q">{path === "ha" ? (two ? "Pick the sensor on each side" : split ? "Pick what watches each side" : "Pick it from Home Assistant") : path === "ics" ? "Paste the calendar link" : path === "mqtt" ? "Where it publishes" : "Set up the pretend device"}</h2>
      {tile === "presence" && <div className="seg addflow-seg" role="group" aria-label="What kind">{[["radar", "Radar (mmWave)"], ["pad", "Pressure pad"], ["motion", "Motion sensor"]].map(([k, l]) =>
        <button type="button" key={k} aria-pressed={kind === k} onClick={() => { setKindSel(k); setEnt({}); }}>{l}</button>)}</div>}
      {tile === "phone" && path !== "sim" && <div className="seg addflow-seg" role="group" aria-label="What it tells">{[["phone", "Home or away"], ["location", "Distance from home"]].map(([k, l]) =>
        <button type="button" key={k} aria-pressed={kind === k} onClick={() => { setKindSel(k); setEnt({}); }}>{l}</button>)}</div>}

      {path === "ha" && haShared === false && <div className="hapick-empty"><p style={{ margin: 0 }}>Connect Home Assistant once (its address and one token) in <Link to="/studio/integrations#ha">Integrations → Home Assistant</Link>. Then come back: everything you have there can be picked from a list.</p></div>}
      {path === "ha" && haShared && !split && <HaPicker kind={kind} value={ent.one?.entity_id ?? ""} onPick={e => onEnt("one", e)} />}
      {split && <>
        <BedZones people={people} active={zoneOn} onActive={setZoneOn} noun={kind === "scale" ? "load cells" : two ? "sensor" : "zone"}
          parts={Object.fromEntries((["left", "right"] as const).map(z => [z, { what: path === "ha" ? ent[z]?.name ?? null : path === "mqtt" ? (z === "left" ? conn.topicL : conn.topicR) || null : "Simulated",
            reading: path === "ha" && ent[z] ? `Now: ${ent[z]!.state ?? "—"}${ent[z]!.unit ? ` ${ent[z]!.unit}` : ""}` : null }]))} />
        {path === "ha" && two && <p className="faint addflow-sub">Tap a side, then pick the sensor by that nightstand (its occupancy or presence entity). The two can be different devices. One side alone is fine too.</p>}
        {path === "ha" && !two && <p className="faint addflow-sub">{kind === "scale" ? "Most bed scales report each side as its own sensor." : "Radars like the Aqara FP2 or an LD2450 board make a zone (or target area) per side: each is its own entity."} Tap a side, then pick what watches it. One side alone is fine too.</p>}
        {path === "ha" && haShared && <HaPicker key={`${pickerKind}-${zoneOn}`} kind={pickerKind} label={`${sideWord(zoneOn)} side`} value={ent[zoneOn]?.entity_id ?? ""} onPick={e => onEnt(zoneOn, e)}
          exclude={[ent[zoneOn === "left" ? "right" : "left"]?.entity_id ?? ""]} />}
      </>}
      {path === "mqtt" && <div className="grid3">
        <label className="fld">Broker<input type="text" value={conn.url} placeholder="mqtt://user:pass@broker.local:1883" onChange={e => setConn({ ...conn, url: e.target.value })} /></label>
        {split ? <><label className="fld">Left side topic<input type="text" value={conn.topicL} placeholder="bed/left/state" onChange={e => setConn({ ...conn, topicL: e.target.value })} /></label>
            <label className="fld">Right side topic<input type="text" value={conn.topicR} placeholder="bed/right/state" onChange={e => setConn({ ...conn, topicR: e.target.value })} /></label></>
          : <label className="fld">Topic<input type="text" value={conn.topic} placeholder="bedroom/sensor/state" onChange={e => setConn({ ...conn, topic: e.target.value })} /></label>}
        <label className="fld">JSON path (optional)<input type="text" value={conn.json} placeholder="targets.0.x" onChange={e => setConn({ ...conn, json: e.target.value })} /></label>
      </div>}
      {path === "ics" && <label className="fld">Calendar link
        <input type="text" autoComplete="off" spellCheck={false} value={conn.ics} placeholder="https://… or webcal://…" onChange={e => setConn({ ...conn, ics: e.target.value })} />
        <span className="dev-howto"><span className="k">Where to find the link</span><ol>
          <li><b>Fastmail</b>: Settings → Calendars → the calendar → Publish → turn on <b>Free/busy information</b> (busy times only, all that's needed) → copy the link.</li>
          <li><b>iCloud</b>: Calendar → share the calendar → Public Calendar → copy link.</li>
          <li><b>Google</b>: the calendar's Settings → Integrate calendar → Secret address in iCal format.</li></ol></span>
        <small className="faint">The link is private: only where it points is shown afterwards.</small></label>}
      {path === "sim" && kind === "scale" && <label className="fld" style={{ maxWidth: 240 }}>Pretend body weight ({wu})<input type="number" value={conn.kg} onChange={e => setConn({ ...conn, kg: e.target.value })} /></label>}
      {path === "sim" && SIM_HELP[kind] && <div className="grid3">
        <label className="fld">{SIM_HELP[kind][0]}<input type="time" value={conn.a} onChange={e => setConn({ ...conn, a: e.target.value })} /></label>
        <label className="fld">{SIM_HELP[kind][1]}<input type="time" value={conn.b} onChange={e => setConn({ ...conn, b: e.target.value })} /></label>
        {kind === "calendar" && <label className="fld">What<input type="text" value={conn.title} placeholder="Concert" onChange={e => setConn({ ...conn, title: e.target.value })} /></label>}
        <p className="fld-hint">{SIM_HELP[kind][2]}</p></div>}

      {roles.length > 1 && <label className="fld" style={{ maxWidth: 320 }}>What it measures<select value={r} onChange={e => setRole(e.target.value)}>{roles.map(x => <option key={x} value={x}>{ROLE_LABEL[x]}</option>)}</select></label>}
      {"invert" in tuning && tuning.invert === true && <label className="haselect-all"><input type="checkbox" checked onChange={() => setTuning(x => ({ ...x, invert: false }))} /> Reads the other way round ("on" means empty): its name suggests so. Untick if not.</label>}
      <div className="grid3">
        <label className="fld">Name<input type="text" value={name} maxLength={80} onChange={e => setName(e.target.value)} /></label>
        <label className="fld">Model (optional)<input type="text" value={model} maxLength={80} placeholder={two ? "Aqara FP1E" : type.examples.split(",")[0]} onChange={e => setModel(e.target.value)} /></label>
      </div>
      <Purpose kind={type.id} compact />
      <Fold id="devices.add.fine" className="fold-sub" title="Fine-tune (optional)" summary="recommended values are already set">
        <TuningFields weightUnit={wu} idPrefix="add" fields={type.tuning.filter(f => f.key !== "ignore_health")} values={tuning} role={r} set={(k, v) => setTuning(x => ({ ...x, [k]: v }))} />
      </Fold>
      {err && <p role="alert" className="addflow-err">{err}</p>}
      {nav(<button type="button" className="btn pri" disabled={busy || !ready} onClick={add}>{busy ? "Adding…" : "Add it"}</button>)}
    </>}
  </div>;
}

/** The end of a path that needs nothing here: what to do on the phone (or in the brand's app), in order. */
function GuideStep({ path, brand, person, oura, say }: { path: PathId; brand: Brand | null; person?: PersonView;
  oura: { configured?: boolean; people?: Record<string, boolean> } | "absent" | null; say: (m: string) => void }) {
  const whose = person ? `${person.name}'s` : "the";
  if (path === "hc") return <><h2 className="addflow-q">On {whose} Android phone</h2>
    <ol className="guide-steps">
      <li>Install Ember &amp; Tide and sign in{person ? ` as ${person.name}` : ""}.</li>
      {brand?.hcHow ? <li>{brand.hcHow}</li> : <li>In the watch or ring's own app, look for Health Connect in its settings and turn it on.</li>}
      <li>In Ember &amp; Tide: Settings → Watch or ring → turn it on and pick what to share (the night group is already on).</li>
    </ol>
    <p className="faint addflow-sub">{brand && brand.hc ? `${brand.name} usually gives ${gives(brand.hc)}${gives(brand.hc, "some") ? `; sometimes ${gives(brand.hc, "some")}` : ""}. ` : ""}It syncs every few hours; nights show in Sleep and help Autopilot.</p></>;
  if (path === "ah") return <><h2 className="addflow-q">Coming with the iPhone app</h2>
    <p className="addflow-sub">The iPhone app will read {brand && brand.ah ? gives(brand.ah) : "your watch or ring"} from Apple Health. Until then{brand?.cloud ? `, connect ${brand.name} straight from its cloud (Back → the direct option), or` : ","} use a Home Assistant entity if you have one.</p></>;
  if (path === "app") return <><h2 className="addflow-q">On {whose} Android phone</h2>
    <ol className="guide-steps"><li>Install Ember &amp; Tide and sign in{person ? ` as ${person.name}` : ""}.</li>
      <li>Settings → From this phone. Turn on what you'd like: home or away, home Wi-Fi, next alarm, charging and bedtime mode, bedside dial.</li>
      <li>Each switch asks for its own permission. Nothing is shared until you turn it on.</li></ol>
    <p className="faint addflow-sub">It then shows here under Phones, with what it shares.</p></>;
  if (path === "webhook") return <><h2 className="addflow-q">Web hook or JSON address</h2>
    <p className="addflow-sub">Set it up in <Link to="/studio/integrations#cloud">Integrations → Cloud accounts</Link>: you get a private address to send readings to, or give one for Ember &amp; Tide to read.</p></>;
  // cloud (Oura)
  const o = oura && oura !== "absent" ? oura : null;
  const connected = !!(person && o?.people?.[person.id]);
  return <><h2 className="addflow-q">{connected ? `${person!.name}'s ${brand?.name ?? "ring"} is connected` : `Connect ${whose} ${brand?.name ?? "ring"}`}</h2>
    {!o ? <p className="addflow-sub">The direct connection arrives in an update of the server.</p>
      : !o.configured ? <p className="addflow-sub">The owner sets up the {brand?.name ?? "Oura"} connection once in <Link to="/studio/integrations#cloud">Integrations → Cloud accounts</Link>. Then come back here to connect each person.</p>
      : connected ? <p className="addflow-sub">Nights arrive a few times a day after waking. Nothing else to do.</p>
      : <><p className="addflow-sub">{person ? `${person.name} signs in` : "Sign in"} to Oura in a new tab and allows sharing. It's easiest on {person ? `${person.name}'s` : "your"} own phone or computer.</p>
        <div className="pills"><button type="button" className="btn pri" onClick={async () => {
          try { const r = await api.post<{ url: string }>("/api/oura/me/connect", { person: person?.id }); window.open(r.url, "_blank", "noopener"); }
          catch (e) { say((e as Error).message); } }}>Connect {brand?.name ?? "Oura"}</button>
          <Tip label="What's shared" text="Sleep stages, heart rate, HRV and temperature change after each night. Tokens stay on this server and are never shown." /></div></>}
  </>;
}
