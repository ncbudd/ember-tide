import Tip from "../components/Tip";
import { kgTo, toKg, type WeightUnit } from "../format";
import { EFFECT, PURPOSE } from "./deviceInfo";

/** Pieces shared by the Devices page, its Tune panel and the Home Assistant entity picker. */
export interface Field { key: string; label: string; type: "number" | "bool" | "select" | "text" | "map"; default?: number | boolean | string | Record<string, string>; unit?: string; help?: string; min?: number; max?: number; step?: number; options?: string[] }
export interface DevType { id: string; label: string; role: string; roles?: string[]; votes: boolean | null; side: boolean; person?: boolean; custom?: boolean; blurb: string; examples: string; tuning: Field[] }
export const ROLE_LABEL: Record<string, string> = { presence: "Presence", weight: "Weight", room_temp: "Room temperature", room_humidity: "Room humidity", wearable_hr: "Heart rate",
  skin_temp: "Skin temperature", wearable_hrv: "HRV", light: "Light", home: "Phone at home", location: "Distance from home", calendar: "Calendar", other: "Other" };

/** Type-specific tuning, drawn from the device type's own field list. */
export function TuningFields({ fields, values, set, role, idPrefix = "tf", mapped = false, weightUnit = "kg" }: { fields: Field[]; values: Record<string, unknown>; set: (k: string, v: unknown) => void; role: string; idPrefix?: string;
  /** the viewer's lb / kg: fields stored in kg are shown and typed in this unit */ weightUnit?: WeightUnit;
  /** a ValueMap is shown next to these fields: it replaces the "Reading is reversed" switch */ mapped?: boolean }) {
  const presenceish = role === "presence" || role === "weight";
  const shown = fields.filter(f => f.type !== "map" && !(mapped && f.key === "invert")
    && (presenceish || !["compare", "threshold", "min_on_s", "hold_s", "invert", "weight"].includes(f.key) || (role === "light" || role === "home") && f.key === "invert"));
  const isKg = (f: Field) => f.type === "number" && f.unit === "kg";
  const show = (f: Field, v: unknown) => isKg(f) && typeof v === "number" ? Math.round(kgTo(v, weightUnit)) : v;   // kg fields in the viewer's unit
  const unitOf = (f: Field) => isKg(f) ? weightUnit : f.unit;
  const recText = (f: Field) => f.type === "bool" ? (f.default ? "on" : "off") : f.key === "max_age_s" && f.default === 0 ? "0 (keep the last state)" : `${show(f, f.default)}${unitOf(f) ? ` ${unitOf(f)}` : ""}`;
  return <div className="tune2">
    {shown.map(f => {
      const v = values[f.key] ?? f.default;
      const id = `${idPrefix}-${f.key}`;
      const changed = f.default !== undefined && String(show(f, v)) !== String(show(f, f.default));
      return <div key={f.key} className="tf">
        <label htmlFor={id} className="tf-text">
          <span className="tf-label">{f.label}{EFFECT[f.key] && <Tip text={EFFECT[f.key]} label={`What changing "${f.label}" does`} />}</span>
          {f.help && <small className="faint">{f.help}</small>}
          {f.default !== undefined && f.type !== "text" && <small className={changed ? "tf-rec changed" : "tf-rec"}>Recommended: {recText(f)}{changed ? " · you changed this" : ""}</small>}
        </label>
        <span className="tf-ctl">
          {f.type === "bool" ? <input id={id} type="checkbox" className="toggle" checked={!!v} onChange={e => set(f.key, e.target.checked)} />
            : f.type === "select" ? <select id={id} value={String(v)} onChange={e => set(f.key, e.target.value)}>{f.options!.map(o => <option key={o} value={o}>{o}</option>)}</select>
            : f.type === "text" ? <input id={id} type="text" value={String(v ?? "")} onChange={e => set(f.key, e.target.value)} />
            : f.key === "weight" ? <><input id={id} type="range" min={0} max={1} step={0.05} value={Number(v)} onChange={e => set(f.key, Number(e.target.value))} /><span className="num tf-unit">{Number(v).toFixed(2)}</span></>
            : isKg(f) ? <><input id={id} type="number" value={show(f, v) as number} step={1}
                min={f.min == null ? undefined : Math.round(kgTo(f.min, weightUnit))} max={f.max == null ? undefined : Math.round(kgTo(f.max, weightUnit))}
                onChange={e => set(f.key, e.target.value === "" ? "" : Math.round(toKg(Number(e.target.value), weightUnit) * 100) / 100)} /><span className="mute tf-unit">{weightUnit}</span></>
            : <><input id={id} type="number" value={v as number} min={f.min} max={f.max} step={f.step} onChange={e => set(f.key, e.target.value === "" ? "" : Number(e.target.value))} /><span className="mute tf-unit">{f.unit}</span></>}
        </span>
      </div>;
    })}
  </div>;
}

/** "What Ember & Tide does with it" + how to calibrate, for a device type. */
export function Purpose({ kind, compact }: { kind: string; compact?: boolean }) {
  const p = PURPOSE[kind] ?? PURPOSE.custom;
  return <div className="dev-purpose">
    <span><b>Used for:</b> {p.uses}</span>
    {p.note && !compact && <span className="faint">{p.note}</span>}
  </div>;
}

/** What an on/off device's raw values mean, per kind: [meaning of "yes", meaning of "no"]. */
const MEANINGS: Record<string, [string, string]> = {
  presence: ["in bed", "empty"], light: ["lights on (awake)", "lights out (sleep)"], home: ["home", "away"],
};
const YES = ["on", "home", "true", "detected", "occupied", "open", "1"];
const NO = ["off", "not_home", "away", "false", "clear", "closed", "unoccupied", "0"];
const NONE = ["unavailable", "unknown", "none", "", "null"];
/** The usual reading of a raw value (what an empty map means), with the old "reversed" flag applied. */
function usualMeaning(raw: string, invert: boolean, role: string): "yes" | "no" | "ignore" {
  const r = raw.toLowerCase();
  const v = YES.includes(r) ? "yes" : NO.includes(r) ? "no" : role === "home" && !NONE.includes(r) ? "no" : "ignore";
  return v === "ignore" ? v : invert ? (v === "yes" ? "no" : "yes") : v;
}

/**
 * "What each reading means" for an on/off device (presence, bedroom light, phone at home): the live raw value, the
 * values it sent lately, and for each one what it means (or ignore it). Saves as tuning.value_map with invert off;
 * an older device with only "Reading is reversed" shows that as its map.
 */
export function ValueMap({ role, live, seen, values, set }: { role: string; live?: string | null; seen: { value: string; ts: number }[];
  values: Record<string, unknown>; set: (k: string, v: unknown) => void }) {
  const [yes, no] = MEANINGS[role] ?? ["yes", "no"];
  const vm = (values.value_map ?? {}) as Record<string, "yes" | "no" | "ignore">;
  const invert = !!values.invert;
  const typical = role === "home" ? ["home", "not_home"] : ["on", "off"];
  const raws = [...new Set([...typical, ...seen.map(x => x.value), ...Object.keys(vm)])].filter(r => r !== "" || seen.some(x => x.value === r));
  const meaning = (r: string) => vm[r] ?? usualMeaning(r, invert, role);
  const change = (r: string, m: "yes" | "no" | "ignore") => {
    const next = Object.fromEntries(raws.map(x => [x, x === r ? m : meaning(x)]));
    set("value_map", next);
    set("invert", false);
  };
  const last = (r: string) => seen.find(x => x.value === r)?.ts;
  return <div className="vmap">
    <span className="tf-label">What each reading means</span>
    <small className="faint">{live != null ? <>Right now it says <b className="vmap-live">{live || "(empty)"}</b>.</> : "No reading yet."} Recommended: {typical[0]} = {yes}, {typical[1]} = {no}.</small>
    {raws.map(r => <div key={r} className="vmap-row">
      <span className="vmap-raw"><code>{r || "(empty)"}</code>{last(r) ? <small className="faint"> · seen {new Date(last(r)! * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</small> : null}</span>
      <div className="seg" role="group" aria-label={`What "${r}" means`}>
        {([["yes", yes], ["no", no], ["ignore", "no reading"]] as const).map(([k, l]) =>
          <button type="button" key={k} aria-pressed={meaning(r) === k} onClick={() => change(r, k)}>{l}</button>)}
      </div>
    </div>)}
  </div>;
}
