import type { ColorKey, NightView, PersonView, Snapshot } from "./types";
import { sideLower } from "./components/BedSidePicker";

export const cToF = (c: number) => c * 9 / 5 + 32;
export const fToC = (f: number) => (f - 32) * 5 / 9;

/** Format a °F value in whole degrees unless explicit digits are requested. */
function fmtF(c: number, digits = 0): string {
  return cToF(c).toFixed(digits);
}

export function temp(c: number | null | undefined, units: "F" | "C" = "F", digits = 0): string {
  if (c == null) return "—";
  return `${units === "F" ? fmtF(c, digits) : c.toFixed(digits)}°`;
}
export function tempNum(c: number | null | undefined, units: "F" | "C" = "F", digits = 0): string {
  if (c == null) return "—";
  return units === "F" ? fmtF(c, digits) : c.toFixed(digits);
}
/** Weights (pets, bed scales) are stored in kg; each person picks lb or kg ("auto" = lb with °F, kg with °C). */
export type WeightUnit = "lb" | "kg";
const KG_LB = 2.20462;
export function weightUnit(s?: { units?: "F" | "C"; weight_units?: "auto" | "lb" | "kg" } | null): WeightUnit {
  const w = s?.weight_units;
  if (w === "lb" || w === "kg") return w;
  return (s?.units ?? "F") === "F" ? "lb" : "kg";
}
export const kgTo = (kg: number, u: WeightUnit) => (u === "lb" ? kg * KG_LB : kg);
export const toKg = (v: number, u: WeightUnit) => (u === "lb" ? v / KG_LB : v);
/** "165 lb" / "75 kg" (digits after the point, default none). */
export function weight(kg: number | null | undefined, u: WeightUnit, digits = 0): string {
  if (kg == null || !Number.isFinite(kg)) return "—";
  return `${kgTo(kg, u).toFixed(digits)} ${u}`;
}

/** One step on the controls: 1 °F or 0.5 °C, returned in °C. */
export const stepC = (units: "F" | "C") => (units === "F" ? 5 / 9 : 0.5);

/** Units for a brand-new person, from the browser's region: °F in the US (and its territories), Liberia and Myanmar, °C elsewhere. */
const F_REGIONS = new Set(["US", "LR", "MM", "PR", "GU", "VI", "AS", "MP", "UM", "BS", "BZ", "KY", "PW", "FM", "MH"]);
export function localeUnits(): "F" | "C" {
  try {
    const tag = (navigator.languages?.[0] || navigator.language || "").trim();
    const region = (new Intl.Locale(tag).maximize().region || "").toUpperCase();
    return F_REGIONS.has(region) ? "F" : "C";
  } catch { return "C"; }
}
/** Does the browser's own locale use a 12-hour clock? */
export function localeClock(): "12" | "24" {
  try { return new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hourCycle?.startsWith("h1") ? "12" : "24"; } catch { return "24"; }
}
/** The signed-in person's clock choice ("12" / "24"); "auto" and unknown leave the browser's own locale in charge. */
let hour12Pref: boolean | undefined;
export function setClockPref(s?: { clock?: "auto" | "12" | "24" } | null) {
  hour12Pref = s?.clock === "12" ? true : s?.clock === "24" ? false : undefined;
}
/** Spread into toLocale*String options that show a time: `{ ...h12(), hour: "numeric" }`. */
export const h12 = (): { hour12?: boolean } => (hour12Pref === undefined ? {} : { hour12: hour12Pref });

export function clock(ts: number | null | undefined, tz: string): string {
  if (ts == null) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], { ...h12(), hour: "numeric", minute: "2-digit", timeZone: tz });
}
export function mins(sec: number): string {
  const m = Math.max(0, Math.round(sec / 60));
  return m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}` : `${m} min`;
}
export function mmss(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export const STATE_TEXT: Record<string, string> = {
  day: "Off until wind-down", waiting: "Pre-conditioning · ready for bed", rhythm: "Following the rhythm",
  out_of_bed_hold: "Out of bed · side kept ready", out_of_bed: "Out of bed · side off", away: "Not home yet · pre-condition waits",
  missed: "Pre-condition stopped · starts when you get in", guest: "Lent to a guest", done: "Done for the night", skipped: "Off tonight", paused: "Paused", no_rhythm: "No rhythm set",
};

export const COLORS: { key: Exclude<ColorKey, "">; name: string }[] = [
  { key: "teal", name: "Teal" }, { key: "orange", name: "Orange" }, { key: "blue", name: "Blue" }, { key: "violet", name: "Violet" },
  { key: "rose", name: "Rose" }, { key: "green", name: "Green" }, { key: "gold", name: "Gold" },
];

/** The person's colour key: their own pick, else the side default (left teal, right orange). */
export function colorKey(p: { zone: string; settings?: { color?: ColorKey } }): Exclude<ColorKey, ""> {
  return p.settings?.color || (p.zone === "left" ? "teal" : "orange");
}

/** CSS colour for a person (follows light/dark/night themes). */
export function personaColor(p: { zone: string; settings?: { color?: ColorKey } } | undefined): string {
  if (!p) return "var(--acc)";
  return `var(--pc-${colorKey(p)})`;
}

export function isNight(p: PersonView, s: Snapshot): boolean {
  const n = p.night, sc = p.schedule;
  if (!sc) return false;
  const inBedNight = (n.state === "rhythm" || n.state === "out_of_bed_hold") && s.now < sc.wake - 15 * 60;
  return inBedNight;
}

export function deltaText(dc: number, units: "F" | "C"): string {
  const v = units === "F" ? dc * 9 / 5 : dc;
  return `${v > 0 ? "+" : ""}${Math.round(v * 10) / 10}°`;
}

/** One calm line about where someone is, from the context devices (phone, location, calendar). Only before the
 *  night has started; null when there's nothing worth saying. */
export function contextText(n: NightView, now: number, tz: string): string | null {
  const c = n.context;
  if (!c || n.started) return null;
  if (c.heading_home && c.eta_min != null) return `On the way home · about ${Math.max(1, Math.round(c.eta_min))} min`;
  if (n.state === "away") return n.away ? null : "Not home yet · pre-condition waits";
  const until = c.event_end ?? c.busy_until;
  if (until && until > now) return `Out until ${clock(until, tz)}`;
  if (c.early_start && c.early_start > now) return `Early start tomorrow · ${clock(c.early_start, tz)}`;
  return null;
}

/** "22:15" → "10:15 PM" */
export function hm12(hm: string): string {
  const [h, m] = hm.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return hm;
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

/** Where a change came from, in plain words ("from the watch"); null for the app itself or when unknown. */
export function sourceText(src: string | null | undefined): string | null {
  switch (src) {
    case "widget": return "from the widget";
    case "tile": return "from the quick settings tile";
    case "watch": return "from the watch";
    case "notification": return "from a notification";
    case "dial": return "from the bedside dial";
    case "ha": return "from a bedside dial or Home Assistant";
    case "voice": return "by voice";
    case "keyboard": return "from the keyboard";
    case "launcher": return "from a launcher shortcut";
    case "autopilot": return "by Autopilot";
    case "studio": return "from Studio";
    case "suggestion": case "guide": return "from a suggestion";
    case "schedule": return "on schedule";
    default: return null;
  }
}

const NIGHT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
/** Rhythm days are the evening a night starts on (0 = Monday). "Sun–Thu nights". */
export function nightsText(days: number[]): string {
  if (days.length === 7) return "Every night";
  const order = [6, 0, 1, 2, 3, 4, 5];                        // Sunday first reads naturally for nights
  const sorted = order.filter(d => days.includes(d));
  const runs: number[][] = [];
  for (const d of sorted) {
    const last = runs[runs.length - 1];
    if (last && order.indexOf(d) === order.indexOf(last[last.length - 1]) + 1) last.push(d); else runs.push([d]);
  }
  const part = (r: number[]) => r.length > 2 ? `${NIGHT_DAYS[r[0]]}–${NIGHT_DAYS[r[r.length - 1]]}` : r.map(d => NIGHT_DAYS[d]).join(" & ");
  return runs.map(part).join(", ") + " nights";
}


/** One raw log row in plain words for Studio's event list ("Hot flash · 15 min"). */
export function eventText(e: { kind: string; source?: string | null; [k: string]: unknown }, units: "F" | "C" = "F"): string {
  const side = e.zone === "left" || e.zone === "right" ? sideLower(e.zone) : typeof e.zone === "string" ? e.zone : "";
  const min = typeof e.minutes === "number" ? ` · ${Math.round(e.minutes)} min` : "";
  switch (e.kind) {
    case "hot_flash": return `Hot flash${min}`;
    case "cold_flash": return `Cold flash${min}`;
    case "flash_extended": return "Flash made 5 min longer";
    case "flash_stopped": return "Flash stopped";
    case "adjust": return typeof e.delta_c === "number" ? `Made ${e.delta_c < 0 ? "cooler" : "warmer"} (${deltaText(e.delta_c, units)})` : "Temperature changed";
    case "adjust_cleared": return "Back to the rhythm";
    case "adjust_ended": return "Change ended, back to the rhythm";
    case "rhythm_started": return e.reason ? "Night started on schedule" : "In bed · night started";
    case "rhythm_restarted": return "Back in bed · night started again from the beginning";
    case "rhythm_changed": return "Rhythm edited";
    case "tonight_skip": return "Turned off for the night";
    case "tonight_end": return "Night ended by hand";
    case "tonight_resume": return "Turned back on";
    case "tonight_pause": return `Paused${min}`;
    case "tonight_start_now": return "Started by hand";
    case "tonight_delay": return `Start delayed${min}`;
    case "tonight_timer": case "timer": return `On by hand with a timer${min}`;
    case "home_changed": return e.home === false ? "Left home" : e.home === true ? "Back home" : "Home or away not known";
    case "feel": return `Said it feels ${String(e.feel ?? "").replace(/_/g, " ")}`.trim();
    case "feedback": return "Morning check-in";
    case "autopilot": return e.status === "applied" ? "Autopilot made a small change" : e.status === "suggested" ? "Autopilot suggested a change" : "Autopilot checked in";
    case "notification": return typeof e.title === "string" ? `Sent: ${e.title}` : "Notification sent";
    case "notification_action": return typeof e.label === "string" ? `Tapped “${e.label}” in a notification` : "Tapped a notification button";
    case "pet_suspected": return `Looked like ${typeof e.pet === "string" && e.pet ? e.pet : "a pet"} ${e.with_person ? "joined someone on the" : "on the"} ${side}, ignored`;
    case "pet_cleared": return `Pet gone from the ${side}`;
    case "guest_started": return `Guest on the ${side}`;
    case "guest_stopped": return `Guest left the ${side}`;
    case "guest_side_off": return `Guest side turned off`;
    case "sides_swapped": return "Sides swapped";
    case "device_health": return `${typeof e.name === "string" ? e.name : "A device"}: ${e.status === "ok" ? "working normally again" : "acting oddly"}`;
    case "leak_detected": return `Leak detected (${typeof e.sensor === "string" && e.sensor ? e.sensor : "water sensor"}): bed turned off`;
    case "leak_cleared": return "Leak hold cleared";
    case "account_created": return "Account created";
    case "console": return "Bed console command";
    case "care": return ({ done: "Care job marked done", snooze: "Care job put off", edit: "Care job changed", add: "Care job added", delete: "Care job removed" } as Record<string, string>)[String(e.action)] ?? "Care list changed";
    case "hub_action": return ({ light: "Hub light changed", quiet_fan: "Hub fan setting changed", restart: "Hub restart requested", water_fill: "Hub fill mode started" } as Record<string, string>)[String(e.action)] ?? "Hub maintenance";
    default: return e.kind.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
  }
}
