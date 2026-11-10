export type View = "me" | "both" | "partner";

export interface FlashSettings { target_c: number; relative: boolean; delta_c: number; duration_min: number; ease_min: number; extend_min: number; max_total_min: number }
export interface AutopilotPolicy { mode: "off" | "suggest" | "act"; max_delta_c: number; floor_c: number; ceiling_c: number; max_actions: number; min_gap_min: number; phases: string[]; quiet_before_wake_min: number; signals: string[]; sensitivity: "low" | "medium" | "high"; respect_manual_min: number; ai_review: boolean; suggest_manual: boolean; suggest_cadence: "morning" | "weekly" | "monthly" | "off"; suggest_weekday: number; learn_nights: number; learn_skip: boolean; learn_since?: number | null; trust: { bed: number; wearable: number; room: number }; trust_more?: TrustMore }
/** "Which to trust more" when the bed and a watch or ring disagree. */
export type TrustMore = "wearable" | "balanced" | "bed";
export interface Learning { need: number; have: number; done: boolean; skipped: boolean; paused?: boolean }
export interface Band { bed_c?: [number, number]; hr?: [number, number]; br?: [number, number]; skin?: [number, number]; restless?: number; feel_rate?: number }
export interface Baseline { nights: number; restless_p90: number; restless_median: number; hr: number | null; skin: number | null; band?: Band }
interface Suggestion { id: string; kind: "manual" | "pattern"; title: string; detail: string; phase: string; proposal: { rhythm_id: string; phase_id: string; field: string; from?: number; to: number } }
interface SuggestLimits { one_per: string; used_this_period: boolean; snooze_nights: number; snoozed: { phase: string; direction: string; until: number }[] }
export interface Suggestions { items: Suggestion[]; waiting: number; cadence: string; next: string | null; learning?: Learning; limits?: SuggestLimits }
export type ColorKey = "" | "teal" | "orange" | "blue" | "violet" | "rose" | "green" | "gold";

export interface PersonSettings {
  units: "F" | "C"; weight_units?: "auto" | "lb" | "kg"; color: ColorKey; app_mode?: "full" | "simple"; welcomed?: boolean; default_view: View; simple_default: boolean; simple_at_night: boolean; simple_red: boolean;
  appearance: "system" | "dark" | "light" | "schedule"; light_from: string; dark_from: string;
  hot_flash: FlashSettings; cold_flash: FlashSettings; autopilot: AutopilotPolicy; partner_can_flash: boolean; partner_can_adjust: boolean;
  notify: { away_precool: boolean; empty_side: boolean; left_on?: boolean; morning: boolean; problems: boolean; pet_question?: boolean; quiet_hours?: boolean };
  tone?: "calm" | "friendly" | "cheeky";
  work_days?: number[]; wearable?: string; setup_steps?: string[];
  pet_questions?: boolean; wake_with_alarm?: boolean; in_bed_after_min?: number | null; up_after_min?: number | null;
}
interface NightContext { home: boolean | null; why: string; distance_km: number | null; eta_min: number | null; heading_home: boolean; busy_until: number | null; event_end: number | null; lights_on: boolean | null; early_start?: number | null }
interface FlashState { kind: "hot" | "cold"; start: number; end: number; ease_end: number; target_c: number; source: string }
export interface GuestSide { since: number; on: boolean; temp_c: number; morning_off_min: number; ends: number | null; dial?: boolean }
export interface NightView {
  state: string; phase_id: string | null; phase_name: string | null; started: boolean; in_bed: number | null;
  base_c: number | null; target_c: number | null; skip: boolean; pause_until: number | null; home: boolean | null; away?: { since: number; until: number | null; by: string | null; held: number } | null; away_check?: { night: string; kind: "away" | "back"; why: string } | null;
  /** Settling in: gate false = the start is held until the bed backs the presence; absent when no bed radar is in use */
  settle?: { gate: boolean; why: string; share?: number } | null; context?: NightContext | null;
  guest?: GuestSide | null;
  flash: FlashState | null; override: { until: string; temp_c: number | null; delta_c: number | null; phase_id: string | null; end_ts?: number | null; manual?: boolean } | null;
}
interface PhaseView { id: string; name: string; start: number; temp_c: number; adjusted_c?: number | null }
export interface PersonView {
  id: string; name: string; persona: "tide" | "ember" | "neutral"; zone: "left" | "right"; settings: PersonSettings;
  night: NightView;
  bed: { on: boolean | null; setpoint_c: number | null; measured_c: number | null };
  presence: { present: boolean | null; fusion: { present: boolean | null; score: number | null; votes: { signal: string; weight: number; value: number }[] } | null; bed_present: boolean | null; starts_in_s: number | null; occupied: boolean | null; since: number | null; echo: boolean; stale: boolean; hr: number | null; br: number | null; status: number | null; status_text: string | null };
  schedule: { bedtime: number; precool: number; window_start: number; window_end: number; wake: number; end: number; rhythm_id: string } | null;
  phases: PhaseView[];
  next: { phase: string; name: string; at: number; temp_c: number | null } | null;
  curve: [number, number][];
  rhythm_curve?: [number, number][] | null;   // the rhythm without tonight's change, when there is one
  actual: [number, number][];
}
export interface Snapshot {
  now: number; tz: string; speed: number;
  system: { driver: string; name: string; online: boolean; detail: string; capabilities: string[]; firmware: string | null; quiet: boolean | null; brightness: number | null; rgb: number[] | null; system_state: string | null; serial?: string | null; age_s?: number | null };
  household: { user_min_c: number; user_max_c: number };
  guest?: Partial<Record<"left" | "right", GuestSide>>;
  zones?: Record<"left" | "right", { present: boolean | null; bed_present: boolean | null }>;
  people: PersonView[];
}
interface Anchor { kind: "bedtime" | "in_bed" | "clock" | "wake"; offset_min: number; time: string | null }
export interface Phase { id: string; name: string; temp_c: number; start: Anchor; ramp_min: number; autopilot: boolean }
export interface Schedule { bedtime: string; wake: string; window_start: string; window_end: string; precool_lead_min: number; confirm_min: number; after_wake_min: number; absent_hold_min: number; start_on_presence: boolean }
export interface Rhythm { id: string; name: string; days: number[]; schedule: Schedule; phases: Phase[]; nights?: "" | "work" | "off" }
export interface EventRow { ts: number; kind: string; person: string | null; source: string | null; [k: string]: unknown }
