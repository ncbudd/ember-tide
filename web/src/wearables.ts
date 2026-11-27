/**
 * Which watch or ring gives what, and how, on each phone. Used by first-time setup and Settings to say plainly
 * what each path brings. Also documented in docs/onboarding.md (keep the two in step).
 *
 * Paths:
 *  - "hc"    Android: the brand's app writes to Health Connect, our Android app reads it (EmberHealth plugin).
 *  - "ah"    iPhone: the brand's app writes to Apple Health, the iPhone app reads it (HealthKit; not released yet).
 *  - "cloud" straight from the brand's cloud, server side (Oura, Withings, WHOOP, Polar, and Garmin with the person's
 *            own key). The fallback for data a phone's health store doesn't carry, or for a phone without our native
 *            app (an iPhone using the web app).
 *
 * Levels: "yes" = reliably written; "some" = depends on the model, app version or a setting; "no" = not written.
 * From vendor pages and community reports as of late 2026; brands change what they share, so the UI says "usually".
 */

export type Level = "yes" | "some" | "no";
export type Field = "stages" | "hr" | "hrv" | "temp" | "resp";
const FIELD_NAMES: Record<Field, string> = {
  stages: "sleep stages", hr: "heart rate", hrv: "heart rate variability", temp: "skin temperature", resp: "breathing rate",
};

export interface Brand {
  id: string;
  name: string;
  icon: string;                                  // a short glyph for the tile
  hc: Partial<Record<Field, Level>> | null;      // null = doesn't write to Health Connect
  ah: Partial<Record<Field, Level>> | null;      // null = doesn't write to Apple Health
  cloud?: Partial<Record<Field, Level>>;         // our direct cloud connection (server: core/embertide/cloud/<id>.py)
  cloudId?: string;                              // the cloud provider id, when there is one
  hcHow?: string;                                // the brand-specific step on Android
  ahHow?: string;                                // the brand-specific step on iPhone
}

export const BRANDS: Brand[] = [
  { id: "oura", name: "Oura ring", icon: "◯",
    hc: { stages: "yes", hr: "yes", hrv: "some", temp: "some", resp: "no" },
    ah: { stages: "yes", hr: "yes", hrv: "yes", temp: "some", resp: "yes" },
    cloud: { stages: "yes", hr: "yes", hrv: "yes", temp: "yes", resp: "no" }, cloudId: "oura",
    hcHow: "In the Oura app: Settings → Health Connect → turn it on and allow everything.",
    ahHow: "In the Oura app: Settings → Apple Health → turn it on and allow everything." },
  { id: "apple", name: "Apple Watch", icon: "⌚",
    hc: null,
    ah: { stages: "yes", hr: "yes", hrv: "yes", temp: "yes", resp: "yes" },
    ahHow: "Nothing to do in another app: the watch saves to Apple Health. Turn on Sleep tracking in the Watch app (wrist temperature needs Series 8 or later)." },
  { id: "pixel", name: "Pixel Watch / Fitbit", icon: "◐",
    hc: { stages: "yes", hr: "yes", hrv: "yes", temp: "some", resp: "yes" },
    ah: null,
    hcHow: "In the Fitbit app: You → Settings → Health Connect → turn on syncing and allow sleep, heart rate and the rest." },
  { id: "samsung", name: "Galaxy Watch", icon: "◑",
    hc: { stages: "yes", hr: "yes", hrv: "some", temp: "some", resp: "some" },
    ah: null,
    hcHow: "In Samsung Health: ⋮ → Settings → Health Connect → allow Samsung Health to write sleep, heart rate and the rest." },
  { id: "garmin", name: "Garmin", icon: "△",
    hc: { stages: "yes", hr: "yes", hrv: "no", temp: "no", resp: "some" },
    ah: { stages: "some", hr: "yes", hrv: "no", temp: "no", resp: "some" },
    // the cloud gives minutes per stage (no timeline), so stages are "some" (Personal API key, cloud/garmin.py)
    cloud: { stages: "some", hr: "yes", hrv: "yes", temp: "no", resp: "some" }, cloudId: "garmin",
    hcHow: "In Garmin Connect: More → Settings → Connected Apps → Health Connect → turn it on.",
    ahHow: "In Garmin Connect: More → Settings → Connected Apps → Apple Health → turn it on." },
  { id: "whoop", name: "Whoop", icon: "▭",
    hc: { stages: "some", hr: "yes", hrv: "no", temp: "some", resp: "some" },
    ah: { stages: "some", hr: "yes", hrv: "no", temp: "no", resp: "some" },
    // WHOOP exposes per-night totals, not the times of individual stages.
    cloud: { stages: "some", hr: "no", hrv: "yes", temp: "yes", resp: "yes" }, cloudId: "whoop",
    hcHow: "In the Whoop app: Profile → Settings → Integrations → Health Connect.",
    ahHow: "In the Whoop app: Profile → Settings → Integrations → Apple Health." },
  { id: "withings", name: "Withings", icon: "◇",
    hc: { stages: "some", hr: "yes", hrv: "no", temp: "some", resp: "some" },
    ah: { stages: "some", hr: "yes", hrv: "no", temp: "some", resp: "some" },
    cloud: { stages: "yes", hr: "yes", hrv: "no", temp: "no", resp: "yes" }, cloudId: "withings",
    hcHow: "In Withings Health Mate: Profile → Health Connect → turn it on.",
    ahHow: "In Withings Health Mate: Profile → Apple Health → turn it on." },
  { id: "polar", name: "Polar", icon: "◎",
    hc: { stages: "some", hr: "yes", hrv: "no", temp: "no", resp: "no" },
    ah: { stages: "some", hr: "yes", hrv: "no", temp: "no", resp: "no" },
    cloud: { stages: "yes", hr: "yes", hrv: "yes", temp: "no", resp: "yes" }, cloudId: "polar",
    hcHow: "In Polar Flow: Settings → General settings → Health Connect → turn it on.",
    ahHow: "In Polar Flow: Settings → General settings → Apple Health → turn it on." },
  { id: "other", name: "Other / none", icon: "·", hc: null, ah: null },
];

/** "sleep stages, heart rate and HRV" from a path's table: only what it reliably ("yes") gives. */
export function gives(t: Partial<Record<Field, Level>> | null | undefined, level: Level = "yes"): string {
  if (!t) return "";
  const f = (Object.keys(FIELD_NAMES) as Field[]).filter(k => t[k] === level).map(k => FIELD_NAMES[k]);
  return f.length <= 1 ? f.join("") : `${f.slice(0, -1).join(", ")} and ${f[f.length - 1]}`;
}

export interface WearableStatus {
  name: string;
  kind: string;
  last: number;
  quiet: boolean;
}

function dayWord(ts: number, nowS: number, tz?: string): string {
  let opt: Intl.DateTimeFormatOptions | undefined = tz ? { timeZone: tz } : undefined;
  const dayMs = (t: number) => {
    const parts = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", ...opt }).format(new Date(t * 1000)).split("-");
    return Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  };
  let diff: number;
  try {
    diff = Math.round((dayMs(nowS) - dayMs(ts)) / 86400000);
  } catch {
    opt = undefined;
    diff = Math.round((dayMs(nowS) - dayMs(ts)) / 86400000);
  }
  if (diff <= 0) return "today";
  if (diff === 1) return "yesterday";
  if (diff < 7) return new Date(ts * 1000).toLocaleDateString("en-US", { weekday: "long", ...opt });
  return new Date(ts * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric", ...opt });
}

/** One-line summary of the watch or ring sources reporting lately, falling back to the picked brand or "None yet". */
export function wearableSummary(sources: WearableStatus[] | null | undefined, brandId?: string | null, tz?: string, nowS = Date.now() / 1000): string {
  if (sources && sources.length > 0) {
    const active = sources.filter(s => !s.quiet);
    const quiet = sources.filter(s => s.quiet);
    const activeText = active.length > 0
      ? `${active.map(s => s.name).join(", ")} · ${dayWord(Math.max(...active.map(s => s.last)), nowS, tz)}`
      : "";
    const quietText = quiet.length > 0
      ? `${quiet.map(s => s.name).join(", ")} (quiet since ${dayWord(Math.max(...quiet.map(s => s.last)), nowS, tz)})`
      : "";
    return [activeText, quietText].filter(Boolean).join("; ");
  }
  return BRANDS.find(b => b.id === brandId)?.name ?? "None yet";
}
