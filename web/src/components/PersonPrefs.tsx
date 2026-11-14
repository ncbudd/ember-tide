import { useEffect, useState } from "react";
import { api } from "../api";
import { colorKey, COLORS, weightUnit } from "../format";
import type { PersonSettings, PersonView } from "../types";

/** Small personal settings shared by the phone's Settings and Studio → Your settings. */

/** The palette as round swatches; a note when it's the same colour as the partner's. */
export function ColorSwatches({ zone, s, partner, onPick }: {
  zone: string; s: PersonSettings; partner?: PersonView; onPick: (key: PersonSettings["color"]) => void;
}) {
  const mine = colorKey({ zone, settings: s });
  return <>
    <div className="swatches" role="radiogroup" aria-label="Your colour">
      {COLORS.map(c => {
        const on = mine === c.key;
        return <button key={c.key} type="button" role="radio" aria-checked={on} aria-label={c.name} title={c.name}
          style={{ background: `var(--pc-${c.key})` }} onClick={() => onPick(c.key)}>{on ? "✓" : ""}</button>;
      })}
    </div>
    {partner && colorKey(partner) === mine &&
      <span className="mute" style={{ fontSize: 12.5 }}>Same as {partner.name}'s. Fine, but a different one makes the chart easier to read.</span>}
  </>;
}

/** °F / °C. */
export function TempUnits({ s, onChange, width = 120 }: { s: PersonSettings; onChange: (u: "F" | "C") => void; width?: number }) {
  return <div className="seg" style={{ width }} role="group" aria-label="Temperature units">
    {(["F", "C"] as const).map(k => <button key={k} type="button" aria-pressed={s.units === k} onClick={() => onChange(k)}>°{k}</button>)}
  </div>;
}

/** 1° / ½° step size when using °F. */
export function StepF({ s, onChange, width = 120 }: { s: PersonSettings; onChange: (v: 1 | 0.5) => void; width?: number }) {
  const cur = s.step_f ?? 1;
  return <div className="seg" style={{ width }} role="group" aria-label="Temperature steps">
    {([[1, "1°"], [0.5, "½°"]] as const).map(([v, label]) => <button key={v} type="button" aria-pressed={cur === v} onClick={() => onChange(v)}>{label}</button>)}
  </div>;
}

/** 12 h / 24 h clock. Automatic follows this device's own language settings. */
export function ClockPick({ s, onChange, width = 190 }: { s: PersonSettings; onChange: (c: "auto" | "12" | "24") => void; width?: number }) {
  const cur = s.clock ?? "auto";
  return <div className="seg" style={{ width }} role="group" aria-label="Clock">
    {([["auto", "Auto"], ["12", "12 h"], ["24", "24 h"]] as const).map(([k, l]) => <button key={k} type="button" aria-pressed={cur === k} onClick={() => onChange(k)}>{l}</button>)}
  </div>;
}

/** The language the assistant and reviews answer in. The screens themselves are English for now. */
export const LANGUAGES: [string, string][] = [["auto", "Automatic"], ["en", "English"], ["de", "Deutsch"], ["fr", "Français"], ["es", "Español"], ["it", "Italiano"],
  ["pt", "Português"], ["pt-BR", "Português (Brasil)"], ["nl", "Nederlands"], ["sv", "Svenska"], ["da", "Dansk"], ["nb", "Norsk"], ["fi", "Suomi"], ["pl", "Polski"],
  ["cs", "Čeština"], ["tr", "Türkçe"], ["ja", "日本語"], ["ko", "한국어"], ["zh", "中文 (简体)"], ["zh-TW", "中文 (繁體)"], ["hi", "हिन्दी"], ["ar", "العربية"], ["he", "עברית"]];
export function LanguagePick({ s, onChange }: { s: PersonSettings; onChange: (l: string) => void }) {
  const cur = s.language ?? "auto";
  const known = LANGUAGES.some(([k]) => k === cur);
  return <select aria-label="Assistant language" value={cur} onChange={e => onChange(e.target.value)} style={{ maxWidth: 190 }}>
    {!known && <option value={cur}>{cur}</option>}
    {LANGUAGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
  </select>;
}

/** The server's time zone (so people can check it) and the hemisphere used for season words in reviews. */
export function RegionRows({ canEdit }: { canEdit: boolean }) {
  const [r, setR] = useState<{ timezone: string; timezone_source?: string; now: string; hemisphere_setting: string; hemisphere: string } | null>(null);
  useEffect(() => { api.get<typeof r>("/api/household/region").then(setR).catch(() => {}); }, []);
  if (!r) return null;
  const save = (hemisphere: string) => api.put<typeof r>("/api/household/region", { hemisphere }).then(setR).catch(() => {});
  return <>
    <div className="row"><span>Time zone<br /><small className="mute">What the server uses for bedtimes. Set it with ET_TZ (see the docs).</small></span><span className="num">{r.timezone}{r.timezone_source ? <small className="mute"> ({r.timezone_source})</small> : null}</span></div>
    <div className="row"><span>Seasons<br /><small className="mute">Which half of the world you are in, for "winter" or "summer" in reviews. Automatic comes from the time zone.</small></span>
      <select aria-label="Hemisphere" disabled={!canEdit} value={r.hemisphere_setting} onChange={e => save(e.target.value)}>
        <option value="auto">Automatic ({r.hemisphere === "none" ? "no seasons" : r.hemisphere === "south" ? "southern" : "northern"})</option>
        <option value="north">Northern hemisphere</option><option value="south">Southern hemisphere</option><option value="none">No seasons (tropics)</option>
      </select></div>
  </>;
}

/** lb / kg for pets and bed scales. Until one is picked ("auto") it follows the temperature units: lb with °F, kg with °C. */
export function WeightUnits({ s, onChange, width = 120 }: { s: PersonSettings; onChange: (u: "lb" | "kg") => void; width?: number }) {
  const cur = weightUnit(s);
  return <div className="seg" style={{ width }} role="group" aria-label="Weight units">
    {(["lb", "kg"] as const).map(k => <button key={k} type="button" aria-pressed={cur === k} onClick={() => onChange(k)}>{k}</button>)}
  </div>;
}

/** Ember / Tide / in between: the same words in Settings, Studio and first-time setup. */
const PERSONAS = [["tide", "Run hot · Tide"], ["ember", "Run cold · Ember"], ["neutral", "In between"]] as const;
export const PERSONA_TIP = "Tide sleepers run hot and like it cooler; Ember sleepers run cold and like it warmer. It sets a starting point; your rhythm and Autopilot tune from there.";
export function PersonaPicker({ value, onChange }: { value: string; onChange: (v: "tide" | "ember" | "neutral") => void }) {
  return <div className="seg" role="group" aria-label="How you sleep">
    {PERSONAS.map(([k, l]) => <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)}>{l}</button>)}
  </div>;
}

const LOOKS = [["system", "Automatic"], ["dark", "Dark"], ["light", "Light"], ["schedule", "By time"]] as const;
const appearanceText = (a: PersonSettings["appearance"]) =>
  a === "system" ? "Follows the phone or computer's own light or dark setting." : a === "schedule" ? "Light during the day, dark in the evening, on your times." : `Always ${a}.`;
export const appearanceShort = (a: PersonSettings["appearance"]) => (LOOKS.find(([k]) => k === a)?.[1] ?? a);

/** Light or dark, with the two times when it goes by the clock. */
export function AppearancePicker({ s, onChange }: { s: PersonSettings; onChange: (p: Partial<PersonSettings>) => void }) {
  return <div style={{ display: "grid", gap: 8 }}>
    <div className="seg" role="group" aria-label="Light or dark">
      {LOOKS.map(([k, l]) => <button key={k} type="button" aria-pressed={s.appearance === k} onClick={() => onChange({ appearance: k })}>{l}</button>)}
    </div>
    <span className="mute" style={{ fontSize: 12.5 }}>{appearanceText(s.appearance)}</span>
    {s.appearance === "schedule" && <div className="row" style={{ justifyContent: "flex-start", flexWrap: "wrap", border: 0, gap: 12 }}>
      <label className="fld">Light from<input type="time" value={s.light_from} onChange={e => e.target.value && onChange({ light_from: e.target.value })} /></label>
      <label className="fld">Dark from<input type="time" value={s.dark_from} onChange={e => e.target.value && onChange({ dark_from: e.target.value })} /></label>
    </div>}
  </div>;
}

/** The Simple (night) screen: when it opens, and red text. */
export function SimpleScreenRows({ s, onChange }: { s: PersonSettings; onChange: (p: Partial<PersonSettings>) => void }) {
  const row = (k: "simple_default" | "simple_at_night" | "simple_red", label: string, sub: string) =>
    <div className="row" key={k}><span>{label}<br /><span className="mute" style={{ fontSize: 12 }}>{sub}</span></span>
      <input type="checkbox" className="toggle" aria-label={label} checked={!!s[k]} onChange={e => onChange({ [k]: e.target.checked })} /></div>;
  return <>
    {row("simple_default", "Night screen by default", "No chart, just the temperature and flashes")}
    {row("simple_at_night", "Switch to the night screen once I'm in bed", "It stays until you tap Full app")}
    {row("simple_red", "Red text on the night screen", "The night screen is always black. This makes its text dim red too, easier on sleepy eyes")}
  </>;
}

/** What the partner may do on this person's side. */
export function SharingRows({ s, partnerName, whose = "my", onChange }: {
  s: PersonSettings; partnerName?: string; whose?: string; onChange: (p: Partial<PersonSettings>) => void;
}) {
  const who = partnerName ?? "Your partner";
  return <>
    <div className="row"><span>{who} can start a hot or cold flash on {whose} side</span>
      <input type="checkbox" className="toggle" aria-label="Partner can start a flash" checked={s.partner_can_flash} onChange={e => onChange({ partner_can_flash: e.target.checked })} /></div>
    <div className="row"><span>{who} can change {whose} temperature</span>
      <input type="checkbox" className="toggle" aria-label="Partner can change the temperature" checked={s.partner_can_adjust} onChange={e => onChange({ partner_can_adjust: e.target.checked })} /></div>
  </>;
}
export const sharingSummary = (s: PersonSettings) =>
  s.partner_can_flash || s.partner_can_adjust ? `Shares ${[s.partner_can_flash && "flashes", s.partner_can_adjust && "temperature"].filter(Boolean).join(" and ")}` : "Nothing shared";
