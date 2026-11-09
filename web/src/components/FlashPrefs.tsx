import { cToF, fToC } from "../format";
import type { FlashSettings, PersonSettings } from "../types";

/** −/+ stepper with limits (phone Settings, Studio → Your settings). No typing, so nothing out of range can be saved. */
export function Steps({ value, min, max, step, fmt, onChange, label }: { value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number) => void; label: string }) {
  const set = (v: number) => onChange(Math.min(max, Math.max(min, Math.round(v / step) * step)));
  return (
    <span className="stepper">
      <button type="button" aria-label={`Less ${label}`} disabled={value <= min} onClick={() => set(value - step)}>−</button>
      <span className="v num">{fmt(value)}</span>
      <button type="button" aria-label={`More ${label}`} disabled={value >= max} onClick={() => set(value + step)}>+</button>
    </span>
  );
}

export function TempSteps({ c, units, onChange, minF = 50, maxF = 104, label }: { c: number; units: "F" | "C"; onChange: (c: number) => void; minF?: number; maxF?: number; label: string }) {
  if (units === "F") return <Steps label={label} value={Math.round(cToF(c))} min={minF} max={maxF} step={1} fmt={v => `${v}°`} onChange={v => onChange(fToC(v))} />;
  const v = Math.round(c * 2) / 2;
  return <Steps label={label} value={v} min={Math.ceil(fToC(minF) * 2) / 2} max={Math.floor(fToC(maxF) * 2) / 2} step={0.5} fmt={x => `${x.toFixed(1)}°`} onChange={onChange} />;
}

/** A temperature difference (not a temperature): whole °F or half °C. */
function DeltaSteps({ c, units, onChange, label, minF = 1, maxF = 15 }: { c: number; units: "F" | "C"; onChange: (c: number) => void; label: string; minF?: number; maxF?: number }) {
  return units === "F"
    ? <Steps label={label} value={Math.round(c * 9 / 5)} min={minF} max={maxF} step={1} fmt={v => `${v}°`} onChange={v => onChange(v * 5 / 9)} />
    : <Steps label={label} value={Math.round(c * 2) / 2} min={0.5} max={Math.floor(maxF * 5 / 9 * 2) / 2} step={0.5} fmt={v => `${v.toFixed(1)}°`} onChange={onChange} />;
}

export const minFmt = (v: number) => `${v} min`;

/** One flash's settings: where it goes, how long, easing back, extra presses and the longest it can run. */
export function FlashBlock({ label, color, f, units, set, hot }: { label: string; color: string; f: FlashSettings; units: "F" | "C"; set: (f: FlashSettings) => void; hot: boolean }) {
  return (
    <div className="card list">
      <div className="row"><b style={{ color, fontWeight: 500 }}>{label}</b>
        <div className="seg" style={{ width: 200 }} role="group" aria-label={`${label}: goes to`}>
          <button type="button" aria-pressed={!f.relative} onClick={() => set({ ...f, relative: false })}>Set temperature</button>
          <button type="button" aria-pressed={!!f.relative} onClick={() => set({ ...f, relative: true })}>{hot ? "Cooler by" : "Warmer by"}</button>
        </div></div>
      {f.relative
        ? <div className="row"><span>{hot ? "Cooler than right now by" : "Warmer than right now by"}</span><DeltaSteps label="difference" c={f.delta_c} units={units} onChange={c => set({ ...f, delta_c: c })} /></div>
        : <div className="row"><span>Goes to</span><TempSteps label="target" c={f.target_c} units={units} minF={hot ? 50 : 70} maxF={hot ? 70 : 104} onChange={c => set({ ...f, target_c: c })} /></div>}
      <div className="row"><span>Lasts</span><Steps label="minutes" value={f.duration_min} min={5} max={60} step={5} fmt={minFmt} onChange={v => set({ ...f, duration_min: v, max_total_min: Math.max(f.max_total_min, v) })} /></div>
      <div className="row"><span>Ease back over</span><Steps label="ease" value={f.ease_min} min={0} max={15} step={1} fmt={v => v === 0 ? "at once" : minFmt(v)} onChange={v => set({ ...f, ease_min: v })} /></div>
      <div className="row"><span>Each extra press adds</span><Steps label="extra minutes" value={f.extend_min} min={1} max={15} step={1} fmt={minFmt} onChange={v => set({ ...f, extend_min: v })} /></div>
      <div className="row"><span>Never longer than<br /><span className="mute" style={{ fontSize: 12 }}>Even with extra presses</span></span><Steps label="longest" value={f.max_total_min} min={Math.max(15, f.duration_min)} max={180} step={15} fmt={minFmt} onChange={v => set({ ...f, max_total_min: v })} /></div>
    </div>
  );
}

/** "Hot 55° · Cold 86° · 15 min" for a collapsed section. */
export function flashSummary(s: PersonSettings): string {
  const u = s.units;
  const deg = (c: number) => u === "F" ? `${Math.round(cToF(c))}°` : `${(Math.round(c * 2) / 2).toFixed(1)}°`;
  const dd = (c: number) => u === "F" ? `${Math.round(c * 9 / 5)}°` : `${(Math.round(c * 2) / 2).toFixed(1)}°`;
  const one = (f: FlashSettings, hot: boolean) => f.relative ? `${dd(f.delta_c)} ${hot ? "cooler" : "warmer"}` : deg(f.target_c);
  return `Hot ${one(s.hot_flash, true)} · Cold ${one(s.cold_flash, false)} · ${s.hot_flash.duration_min} min`;
}
