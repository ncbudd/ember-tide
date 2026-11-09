import { useBedFromFoot } from "../components/BedSidePicker";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import Fold from "../components/Fold";
import Tip from "../components/Tip";
import { useApp } from "../ctx";
import { temp } from "../format";
import type { PersonView } from "../types";
import { personLabel, sideWord } from "./deviceKinds";
import { agoText } from "./PhoneRows";

/**
 * Bedside dials: see the dials Ember & Tide already knows (through Home Assistant and their Bluetooth beacons) and
 * which side each is by, or set one up from scratch: pick the hardware → one dial or two → copy the ready-made
 * ESPHome file → wait for it to appear, then turn the knob to test. Never asks for the Home Assistant token again:
 * it uses the saved connection.
 */

export interface Dial {
  id: string; name: string; model: string | null; firmware: string | null; online: boolean; entities: number; side: "left" | "right" | null;
  /** what the dial's own "Home side" sensor says (newer firmware); side_from: "set" in Studio, "dial" reported, null not known */
  reported_side?: "left" | "right" | null; side_from?: "set" | "dial" | "beacon" | null; hidden?: boolean; last_seen?: number | null;
  /** its Bluetooth beacon number (from the firmware, or stored), and which phone heard it last */
  beacon?: number | null; beacon_from?: "dial" | "set" | null; heard?: { person: string; rssi: number; seen_at: number } | null;
}
interface Beacon { minor: number; side: string; name: string; heard: { person: string; rssi: number; seen_at: number } | null; dial?: { id: string; name: string } | null }
export interface DialsInfo { ha_configured: boolean; ha_error: string | null; bridge: string; dials: Dial[]; beacons: Beacon[] }
type Mode = "start" | "found" | "hw" | "count" | "yaml" | "wait";
interface Plan { side: "left" | "right"; name: string }

const HW: { id: string; name: string; size: string; chip: string; ok: boolean; note: string; draw: "crow" | "m5" | "wave" | "board" }[] = [
  { id: "crowpanel", name: "Elecrow CrowPanel 2.1\" round", size: "2.1\" · 480×480 touch screen, knob", chip: "ESP32-S3 · ST7701S", ok: true, note: "The one Ember & Tide supports today.", draw: "crow" },
  { id: "m5dial", name: "M5Stack Dial", size: "1.28\" · 240×240 touch screen, knob", chip: "ESP32-S3 · GC9A01", ok: false, note: "Coming: the screen layout needs a smaller version.", draw: "m5" },
  { id: "waveshare", name: "Waveshare ESP32-S3 round touch", size: "1.28\" / 1.8\" / 2.1\" round touch screens", chip: "ESP32-S3", ok: false, note: "Coming: no knob on most models, so it needs a touch-only layout.", draw: "wave" },
  { id: "other", name: "Other ESP32 display (advanced)", size: "Any ESPHome screen with LVGL", chip: "ESP32-S3 with PSRAM", ok: true, note: "Start from the shared package and replace its display, touch and knob sections with your board's.", draw: "board" },
];

function Drawing({ kind }: { kind: "crow" | "m5" | "wave" | "board" }) {
  return <svg className="hw-draw" viewBox="0 0 64 64" width="64" height="64" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
    {kind === "board" ? <><rect x="8" y="14" width="48" height="36" rx="3" /><rect x="14" y="19" width="30" height="22" rx="2" /><path d="M50 22v4M50 30v4M50 38v4" /></>
      : <><circle cx="32" cy="32" r={kind === "crow" ? 26 : kind === "m5" ? 22 : 24} />{kind !== "wave" && <circle cx="32" cy="32" r={kind === "crow" ? 22 : 18} strokeDasharray="2 3" />}
        <circle cx="32" cy="32" r={kind === "crow" ? 17 : kind === "m5" ? 13 : 19} /><path d="M27 33h10M32 28v10" opacity=".5" /></>}
  </svg>;
}

async function copyText(t: string, say: (m: string) => void) {
  try { await navigator.clipboard.writeText(t); say("Copied"); return; } catch { /* plain http on the home network has no clipboard API */ }
  const ta = document.createElement("textarea"); ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select();
  let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove(); say(ok ? "Copied" : "Select the text and copy it");
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 30) || "bedside-dial";
const shownDials = (d: DialsInfo | null) => (d?.dials ?? []).filter(x => !x.hidden);
export const dialSummary = (d: DialsInfo | null) => { if (!d) return "…"; const ds = shownDials(d);
  return ds.length ? `${ds.length} dial${ds.length === 1 ? "" : "s"} · ${ds.filter(x => x.online).length} online` : "none found yet"; };
const WEEK = 7 * 86400;
const dayText = (ts: number) => new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: new Date(ts * 1000).getFullYear() === new Date().getFullYear() ? undefined : "numeric" });

export default function DialSetup({ people, onClose, onBack, embedded = false }: { people: PersonView[]; onClose?: () => void; onBack?: () => void; embedded?: boolean }) {
  useBedFromFoot();                                   // left/right labels as you lie (bed orientation)
  const { snap, say } = useApp();
  const [info, setInfo] = useState<DialsInfo | null>(null);
  const [mode, setMode] = useState<Mode | null>(null);
  const [oids, setOids] = useState<Record<string, string>>({});
  const [hw, setHw] = useState("crowpanel");
  const [plan, setPlan] = useState<Plan[]>([]);
  const [pkg, setPkg] = useState<{ yaml: string; h: string } | null>(null);
  const known = useRef<Set<string>>(new Set());
  const [found, setFound] = useState<Dial | null>(null);
  const [hideAsk, setHideAsk] = useState<string | null>(null);
  const load = () => api.get<DialsInfo>("/api/dials").then(d => { setInfo(d); return d; }).catch(() => null);
  useEffect(() => {
    load().then(d => { if (d) { known.current = new Set(d.dials.map(x => x.id)); setMode(m => m ?? (d.dials.length ? "found" : "start")); } else setMode("start"); });
    api.get<{ object_ids: Record<string, string> }>("/api/integrations/ha").then(h => setOids(h.object_ids ?? {})).catch(() => {});
    const t = window.setInterval(() => { if (!document.hidden) load(); }, 20000);   // beacons heard, online/offline
    return () => clearInterval(t);
  }, []);
  // step 5: wait for the new dial to show up in Home Assistant
  useEffect(() => {
    if (mode !== "wait" || found) return;
    const t = window.setInterval(async () => {
      const d = await load();
      if (!d) return;
      const want = plan.map(p => p.name.toLowerCase());
      const hit = d.dials.find(x => want.includes(x.name.toLowerCase())) ?? d.dials.find(x => !known.current.has(x.id));
      if (hit) {
        const side = plan.find(p => p.name.toLowerCase() === hit.name.toLowerCase())?.side ?? plan[0]?.side ?? "left";
        try { await api.put(`/api/dials/${hit.id}`, { side, beacon: side === "left" ? 1 : 2 /* the beacon_minor its file got */ }); } catch { /* shown as not set; can be picked in the list */ }
        setFound({ ...hit, side });
      }
    }, 4000);
    return () => clearInterval(t);
  }, [mode, found, plan.map(p => p.name).join()]);
  useEffect(() => {
    if (mode !== "yaml" || pkg) return;
    Promise.all([fetch("/api/dials/files/embertide-dial-common.yaml").then(r => r.text()), fetch("/api/dials/files/et_dial.h").then(r => r.text())])
      .then(([yaml, h]) => setPkg({ yaml, h })).catch(() => {});
  }, [mode]);
  if (!info || !mode) return <p className="faint">Looking for dials…</p>;

  const left = people.find(p => p.zone === "left"), right = people.find(p => p.zone === "right");
  const oid = (p?: PersonView) => (p && oids[p.id]) || `embertide_${p?.zone ?? "left"}`;
  const yamlFor = (d: Plan) => {
    const s = slug(d.name), k = s.replace(/-/g, "_");
    return [`# Ember & Tide bedside dial: ${d.name} (${d.side} side)`,
      `# Wi-Fi, the API key and the update password come from your ESPHome secrets (Secrets in the ESPHome Device Builder).`,
      `substitutions:`, `  device_name: ${s}`, `  friendly_name: "${d.name.replace(/"/g, "'")}"`,
      `  p0: ${oid(left)}    # left side`, `  p1: ${oid(right)}    # right side`,
      `  home_side: "${d.side === "left" ? 0 : 1}"    # the side it shows first`, `  beacon_minor: "${d.side === "left" ? 1 : 2}"    # lets the phone app know which dial it's next to`,
      ``, `packages:`, `  embertide: !include .embertide-dial-common.yaml`, ``,
      `api:`, `  encryption:`, `    key: !secret ${k}_api_key`, ``, `ota:`, `  - platform: esphome`, `    password: !secret ${k}_ota_password`, ""].join("\n");
  };
  const steps: Mode[] = ["start", "hw", "count", "yaml", "wait"];
  const NAMES: Record<Mode, string> = { start: "Start", found: "Your dials", hw: "Hardware", count: "How many", yaml: "Install", wait: "Test" };
  const bridgeOff = info.bridge !== "connected";
  const notes = <>
    {!info.ha_configured && <p className="dial-note">Dials work through Home Assistant. Connect it once in <Link to="/studio/integrations#ha">Integrations → Home Assistant</Link>, then come back.</p>}
    {info.ha_error && <p className="dial-note bad">{info.ha_error}</p>}
    {info.ha_configured && bridgeOff && <p className="dial-note">Dials read each side from Home Assistant: turn on <b>Show Ember &amp; Tide in Home Assistant</b> in <Link to="/studio/integrations#mqtt">Integrations → Advanced (MQTT)</Link>.</p>}
  </>;
  const setHidden = async (d: Dial, hidden: boolean) => {
    try { await api.put(`/api/dials/${d.id}/hidden`, { hidden }); say(hidden ? `${d.name} hidden` : `${d.name} is back in the list`); setHideAsk(null); load(); }
    catch (x) { say((x as Error).message); }
  };
  const setSide = async (d: Dial, side: "left" | "right" | null) => {
    try { await api.put(`/api/dials/${d.id}`, { side }); say(side ? `${d.name}: by the ${sideWord(side).toLowerCase()} side` : "Side cleared"); load(); } catch (x) { say((x as Error).message); }
  };
  const dialRow = (d: Dial) => {
    const old = !d.online && d.last_seen && Date.now() / 1000 - d.last_seen > WEEK;
    return <div key={d.id} className="dialrow">
      <span className={`sdot ${d.online ? "ok" : "off"}`} aria-hidden />
      <span className="dialrow-id"><b>{d.name}</b><small className="faint">{d.online ? "Online" : "Offline"}{d.firmware ? ` · firmware ${d.firmware.split(" ")[0]}` : ""}{d.model ? ` · ${d.model}` : ""}</small>
        {old && <small className="dial-old">Not seen since {dayText(d.last_seen!)}, probably an old setup.</small>}</span>
      <div className="dialrow-side" role="group" aria-label={`Which side ${d.name} is by`}>
        <span className="dialrow-k">{d.side ? <>By the {sideWord(d.side).toLowerCase()} side{d.side_from === "dial" ? <small className="faint"> · the dial says so</small> : d.side_from === "beacon" ? <small className="faint"> · from its beacon</small> : null}</> : <b className="dialrow-unset">Which side is it by? Tap one</b>}</span>
        <div className="seg">{(["left", "right"] as const).map(z => { const p = people.find(x => x.zone === z);
          return <button type="button" key={z} aria-pressed={d.side === z} aria-label={`${sideWord(z)} side as you lie in bed${p ? `, ${p.name}` : ""}`}
            onClick={() => setSide(d, d.side === z && d.side_from === "set" ? null : z)}>{sideWord(z)}{p && p.name.toLowerCase() !== z ? ` · ${p.name}` : ""}</button>; })}</div>
        {d.beacon
          ? <small className="faint">Bluetooth beacon {d.beacon}{d.heard?.seen_at ? ` · heard by ${people.find(x => x.id === d.heard!.person)?.name ?? "a"}'s phone ${agoText(Date.now() / 1000 - d.heard.seen_at)}` : " · not heard by a phone yet"}</small>
          : <span className="dialrow-beacon"><small className="faint">Its Bluetooth beacon number (in its file as beacon_minor):</small>
              <span className="pills">{[1, 2].map(n => <button type="button" key={n} className="pill" onClick={async () => {
                try { await api.put(`/api/dials/${d.id}`, { side: d.side, beacon: n }); say(`${d.name}: beacon ${n}`); load(); } catch (x) { say((x as Error).message); } }}>{n}</button>)}</span></span>}
      </div>
      <div className="dialrow-acts">
        {hideAsk === d.id
          ? <div className="dial-hide">
              <p>Hide {d.name}? It disappears from Ember &amp; Tide only; you can show it again under Hidden dials.</p>
              <p className="faint">To delete it from Home Assistant too: in <b>ESPHome Device Builder</b>, its card's <b>⋮ → Delete</b>, and in <b>Settings → Devices &amp; services → Devices</b>, open it and delete it.</p>
              <div className="pills"><button type="button" className="pill" style={{ borderColor: "var(--bad)", color: "var(--bad)" }} onClick={() => setHidden(d, true)}>Hide it, not mine anymore</button>
                <button type="button" className="pill" onClick={() => setHideAsk(null)}>Keep</button></div></div>
          : <button type="button" className="pill" aria-label={`Remove ${d.name}`} onClick={() => setHideAsk(d.id)}>Remove</button>}
      </div>
    </div>;
  };
  const nav = (back: () => void, next?: ReactNode) => <div className="pills addflow-nav"><button type="button" className="pill" onClick={back}>Back</button>{next}</div>;

  return <div className="dialsetup">
    {mode !== "found" && <ol className="steps3" aria-label="Steps">{steps.map((s, i) => <li key={s} className={s === mode ? "on" : steps.indexOf(mode) > i ? "done" : ""}>{i + 1}. {NAMES[s]}</li>)}</ol>}

    {mode === "start" && <>
      {!embedded && <h2 className="addflow-q">Bedside dials</h2>}
      <div className="choicegrid one">
        <button type="button" className="choice" onClick={() => setMode("found")}><span className="choice-text"><b>It's already in Home Assistant</b>
          <span>{info.dials.length ? `Found ${info.dials.length}: say which side each one is by.` : "Your dial runs ESPHome and shows in Home Assistant: find it and say which side it's by."}</span></span><span className="choice-go" aria-hidden>›</span></button>
        <button type="button" className="choice" onClick={() => setMode("hw")}><span className="choice-text"><b>Set up a new dial from scratch</b>
          <span>Pick the hardware, copy one ready-made file into ESPHome, and test it.</span></span><span className="choice-go" aria-hidden>›</span></button>
      </div>
      {notes}
      {onBack && nav(onBack)}
    </>}

    {mode === "found" && <>
      {!embedded && <h2 className="addflow-q">Your bedside dials</h2>}
      {notes}
      {shownDials(info).map(d => dialRow(d))}
      {info.dials.some(x => x.hidden) && <Fold id="devices.dials.hidden" className="fold-sub" title={`Hidden dials (${info.dials.filter(x => x.hidden).length})`} summary="Show">
        {info.dials.filter(x => x.hidden).map(d => <div key={d.id} className="row" style={{ fontSize: 13 }}><span>{d.name}<br /><small className="faint">{d.online ? "Online" : "Offline"}{d.firmware ? ` · firmware ${d.firmware.split(" ")[0]}` : ""}</small></span>
          <button type="button" className="pill" onClick={() => setHidden(d, false)}>Show again</button></div>)}
      </Fold>}
      {info.ha_configured && !info.dials.length && <p className="addflow-sub">No dial found in Home Assistant yet. A dial shows up once it's added in Home Assistant (Settings → Devices &amp; services → ESPHome) with "dial" in its name.</p>}
      {/* each dial's Bluetooth beacon is shown on that dial's row; only beacons not linked to any dial are listed here */}
      {info.beacons.some(b => !b.dial) && <Fold id="devices.dials.ble" className="fold-sub" title="Beacons not linked to a dial" summary={`${info.beacons.filter(b => !b.dial).length}`}>
        <p className="faint" style={{ fontSize: 12.5, margin: 0 }}>Each dial also sends a tiny Bluetooth signal, so a phone charging next to it at night counts a little toward that side being in bed.
          <Tip label="About the beacons" text="Each beacon belongs to one dial and follows that dial's side. The Android app listens briefly at night, only while charging or in bedtime mode (Settings → From this phone → Bedside dial)." /></p>
        {info.beacons.filter(b => !b.dial).map(b => <div key={b.minor} className="row" style={{ fontSize: 13 }}><span>Beacon {b.minor} · {sideWord(b.side)} side<br /><small className="faint">pick its number on the dial above</small></span>
          <span className="mute">{b.heard ? "heard" : "not heard yet"}</span></div>)}
      </Fold>}
      <Fold id="devices.dials.legacy" className="fold-sub" title="Advanced: dials on the older firmware" summary="copy and reflash">
        <LegacyDial people={people} oids={oids} units={snap?.people[0]?.settings.units ?? "F"} say={say} />
      </Fold>
      <div className="pills addflow-nav">
        <button type="button" className="pill" onClick={() => setMode("start")}>Back</button>
        <button type="button" className="pill" onClick={() => setMode("hw")}>Set up another dial</button>
        {onClose && <button type="button" className="btn pri" onClick={onClose}>Done</button>}
      </div>
    </>}

    {mode === "hw" && <>
      <h2 className="addflow-q">Which dial do you have?</h2>
      <div className="choicegrid">{HW.map(h => <button type="button" key={h.id} className={`choice hw${hw === h.id ? " on" : ""}`} disabled={!h.ok} onClick={() => { setHw(h.id); setPlan(p => p.length ? p : [{ side: "left", name: left && left.name.toLowerCase() !== "left" ? `${left.name}'s dial` : "Left dial" }]); setMode("count"); }}>
        <Drawing kind={h.draw} /><span className="choice-text"><b>{h.name}{!h.ok && <span className="tag">Coming</span>}</b><span>{h.size}</span><small className="faint">{h.chip} · {h.note}</small></span></button>)}</div>
      {nav(() => setMode("start"))}
    </>}

    {mode === "count" && <>
      <h2 className="addflow-q">One dial or two?</h2>
      <div className="seg addflow-seg" role="group" aria-label="How many dials">
        <button type="button" aria-pressed={plan.length === 1} onClick={() => setPlan(p => p.slice(0, 1))}>One dial</button>
        <button type="button" aria-pressed={plan.length === 2} onClick={() => setPlan(p => [p[0] ?? { side: "left", name: "Left dial" }, { side: p[0]?.side === "right" ? "left" : "right", name: (() => { const q = p[0]?.side === "right" ? left : right; return q && q.name.toLowerCase() !== q.zone ? `${q.name}'s dial` : `${sideWord(p[0]?.side === "right" ? "left" : "right")} dial`; })() }])}>Two: one each side</button>
      </div>
      {plan.map((d, i) => <div key={i} className="grid3">
        <label className="fld">Dial {i + 1}: name<input type="text" maxLength={40} value={d.name} onChange={e => setPlan(ps => ps.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} /></label>
        <label className="fld">By which side<select value={d.side} onChange={e => setPlan(ps => ps.map((x, j) => j === i ? { ...x, side: e.target.value as "left" | "right" } : x))}>
          {people.map(p => <option key={p.id} value={p.zone}>{personLabel(p)}</option>)}</select></label>
      </div>)}
      {hw === "other" && <p className="dial-note">For another board: copy the shared package in the next step, then replace its display, touch and knob sections with your board's (the pages and logic stay the same).</p>}
      {nav(() => setMode("hw"), <button type="button" className="btn pri" disabled={!plan.length || plan.some(p => !p.name.trim()) || (plan.length === 2 && plan[0].side === plan[1].side)} onClick={() => setMode("yaml")}>Next</button>)}
    </>}

    {mode === "yaml" && <>
      <h2 className="addflow-q">Put it on the dial</h2>
      <ol className="guide-steps">
        <li>In Home Assistant: <b>Settings → Add-ons → ESPHome Device Builder</b> (install it from the Add-on store if it isn't there).</li>
        <li><b>Once</b>: in the Builder, open <b>Secrets</b> and add <code>wifi_ssid</code>, <code>wifi_password</code> and, for each dial, the two lines named in its file below (any long random text works for both).</li>
        <li><b>Once</b>: save the two shared files below into the ESPHome folder (<code>/config/esphome/</code>) with the File editor or Studio Code Server add-on, named exactly <code>.embertide-dial-common.yaml</code> and <code>et_dial.h</code>.</li>
        <li>For each dial: <b>+ New device</b> → give it the name below → <b>Skip</b> → <b>Edit</b> → replace everything with the dial's file → <b>Save</b>.</li>
        <li><b>Install</b>: the first time plug the dial into this computer by USB and choose "Plug into this computer" (ESPHome Web); after that updates go over Wi-Fi.</li>
      </ol>
      {plan.map((d, i) => { const y = yamlFor(d); return <div key={i} className="yamlbox">
        <div className="row"><b style={{ fontWeight: 500, fontSize: 13.5 }}>{d.name} · {sideWord(d.side ?? "left").toLowerCase()} side</b><button type="button" className="pill" onClick={() => copyText(y, say)}>Copy</button></div>
        <pre className="codeblock">{y}</pre></div>; })}
      <Fold id="devices.dials.pkg" className="fold-sub" title="The two shared files (once per Home Assistant)" summary="same for every dial">
        <div className="pills"><a className="pill" href="/api/dials/files/embertide-dial-common.yaml" download=".embertide-dial-common.yaml">Download .embertide-dial-common.yaml</a>
          <a className="pill" href="/api/dials/files/et_dial.h" download="et_dial.h">Download et_dial.h</a>
          {pkg && <button type="button" className="pill" onClick={() => copyText(pkg.yaml, say)}>Copy the package</button>}
          {pkg && <button type="button" className="pill" onClick={() => copyText(pkg.h, say)}>Copy et_dial.h</button>}</div>
        <small className="faint">No passwords in them: Wi-Fi comes from your ESPHome secrets. The dial shows each person's name and colour from Ember &amp; Tide.</small>
      </Fold>
      {notes}
      {nav(() => setMode("count"), <button type="button" className="btn pri" onClick={() => { known.current = new Set(info.dials.map(x => x.id)); setFound(null); setMode("wait"); }}>I've installed it</button>)}
    </>}

    {mode === "wait" && <>
      <h2 className="addflow-q">{found ? `Found ${found.name}` : "Waiting for your dial…"}</h2>
      {!found ? <><p className="addflow-sub"><span className="spinner" aria-hidden /> When it starts, Home Assistant offers to add it (Settings → Devices &amp; services → <b>Discovered: ESPHome</b> → Configure). It shows up here a moment later.</p>
          {notes}</>
        : <>{(() => { const p = people.find(x => x.zone === found.side) ?? people[0]; const live = snap?.people.find(x => x.id === p?.id);
            return <div className="dialtest"><p className="addflow-sub">It's by the <b>{found.side}</b> side{p && p.name.toLowerCase() !== p.zone ? ` (${p.name})` : ""}. Turn the knob a click or two: the temperature here should change within a few seconds.</p>
              <div className="dialtest-big" aria-live="polite">{temp(live?.night.target_c ?? live?.bed.setpoint_c, snap?.people[0]?.settings.units ?? "F")}</div>
              <small className="faint">Nothing changing? Check Home Assistant shows the dial as online, and that the bridge is on.</small></div>; })()}</>}
      <div className="pills addflow-nav"><button type="button" className="pill" onClick={() => setMode("yaml")}>Back</button>
        {found && <button type="button" className="pill" onClick={() => { setFound(null); known.current.add(found.id); }}>Wait for the next dial</button>}
        <button type="button" className={found ? "btn pri" : "pill"} onClick={() => { load(); setMode("found"); }}>{found ? "Done" : "Skip for now"}</button></div>
    </>}
  </div>;
}

/** The older Elecrow dial firmware: profile substitutions + hot flash scripts to copy (kept for dials not yet moved). */
function LegacyDial({ people, oids, units, say }: { people: PersonView[]; oids: Record<string, string>; units: "F" | "C"; say: (m: string) => void }) {
  const PH: [string, string][] = [["bedtime", "wind_down"], ["asleep1", "fall_asleep"], ["asleep2", "deep"], ["wakeup", "wake"]];
  const oid = (pid: string) => oids[pid] ?? "embertide_?";
  const dial = people.slice(0, 2).map((p, i) => [`  # ---- Profile ${i}: ${p.name} (bed zone ${p.zone}) ----`, `  p${i}_climate: climate.${oid(p.id)}`,
    `  p${i}_hot_flash: script.${oid(p.id)}_hot_flash    # see the script below`, `  p${i}_phase_sensor: sensor.${oid(p.id)}_phase`,
    ...PH.map(([slot, ph]) => `  p${i}_offset_${slot}: number.${oid(p.id)}_${ph}_temp`)].join("\n")).join("\n\n");
  const scripts = people.slice(0, 2).map(p => `${oid(p.id)}_hot_flash:\n  alias: "${p.name}: hot flash (Ember & Tide)"\n  sequence:\n    - action: button.press\n      target:\n        entity_id: button.${oid(p.id)}_hot_flash`).join("\n");
  return <>
    <span className="mute" style={{ fontSize: 12.5 }}>Only for dials still on the older ESPHome Elecrow firmware: replace the profile substitutions with these and reflash. New dials use the setup above instead.</span>
    <div className="row"><b style={{ fontWeight: 500, fontSize: 13 }}>1 · Dial substitutions</b><button type="button" className="pill" onClick={() => copyText(dial, say)}>Copy</button></div>
    <pre className="codeblock">{dial}</pre>
    <span className="mute" style={{ fontSize: 12.5 }}>The offsets page edits each phase's temperature directly (in °{units}). Set the dial's offset range to 55–95 with a step of 1.</span>
    <div className="row"><b style={{ fontWeight: 500, fontSize: 13 }}>2 · Hot flash scripts (scripts.yaml)</b><button type="button" className="pill" onClick={() => copyText(scripts, say)}>Copy</button></div>
    <pre className="codeblock">{scripts}</pre>
  </>;
}
