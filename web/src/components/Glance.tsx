import { useEffect, useState } from "react";
import { useLayout } from "./layout";
import { api } from "../api";
import { clock, mins, personaColor, STATE_TEXT, tempNum } from "../format";
import type { PersonView, Snapshot } from "../types";

/** Tonight gets this second column on tablets in landscape and on unfolded foldables (book posture). In portrait
 *  Tonight uses the height for a bigger chart and more of the plan; in tabletop posture the bottom half is for the
 *  controls. */
function useWide(): boolean {
  const l = useLayout();
  return l.tab && (l.fold === "h" || (l.land && l.fold !== "v"));
}

interface Live {
  status: string; direction: string; score: number; threshold: number;
  reasons: { signal: string; label: string; value: string; detail: string; push: number }[];
  signals: { room_c?: number | null; humidity?: number | null; restless?: number | null; skin_c?: number | null; [k: string]: unknown };
}
interface NightSum { date: string; label: string; in_progress: boolean; has_data: boolean; in_bed_min?: number; time_to_settle_min?: number | null;
  hot_flashes?: number; cold_flashes?: number; times_up?: number; hr_avg?: number | null; adjustments?: number; feedback?: { temp?: string; rested?: number } | null }
interface ApStatus { policy: { mode: string }; live: Live | null; tonight: { ts: number; delta_c?: number; status?: string; reason?: string }[]; has_skin: boolean }

/** Autopilot writes its reasons in °C; show changes ("+1.4 °C") in the person's units. */
function inUnits(v: string, u: "F" | "C"): string {
  if (u === "C") return v;
  return v.replace(/([+−-])\s*(\d+(?:\.\d+)?)\s*Â?°\s*C/g, (_m, sign, num) => `${sign}${Math.round(parseFloat(num) * 9 / 5)}°F`)
          .replace(/(\d+(?:\.\d+)?)\s*Â?°\s*C/g, (_m, num) => `${Math.round(parseFloat(num) * 9 / 5 + 32)}°F`);
}

const Stat = ({ k, v, sub }: { k: string; v: string; sub?: string }) =>
  <div className="gl-stat"><span className="k">{k}</span><span className="v num">{v}</span>{sub && <small className="mute">{sub}</small>}</div>;

/**
 * The extra column on a big screen: what the bed and room are doing right now, your partner's side, and what Autopilot
 * is watching. Only fetched when it's actually on screen, so phones never pay for it.
 */
export default function Glance({ snap, me, partner }: { snap: Snapshot; me: PersonView; partner: PersonView | null }) {
  const wide = useWide();
  const [ap, setAp] = useState<ApStatus | null>(null);
  const [last, setLast] = useState<NightSum | null>(null);
  useEffect(() => {
    if (!wide) return;
    let stop = false;
    const load = () => api.get<ApStatus>(`/api/people/${me.id}/autopilot`).then(r => { if (!stop) setAp(r); }).catch(() => {});
    load();
    api.get<NightSum[]>(`/api/people/${me.id}/nights`).then(ns => { if (!stop) setLast(ns.find(x => !x.in_progress && x.has_data && (x.in_bed_min ?? 0) >= 30) ?? null); }).catch(() => {});
    const t = window.setInterval(() => { if (document.visibilityState !== "hidden") load(); }, 60_000);
    return () => { stop = true; clearInterval(t); };
  }, [wide, me.id]);
  if (!wide) return null;

  const u = me.settings.units;
  const n = me.night;
  const sig = ap?.live?.signals ?? {};
  const bedVsPlan = me.bed.measured_c != null && n.target_c != null ? me.bed.measured_c - n.target_c : null;
  const live = ap?.live;
  const lean = live?.direction === "cooler" ? "leaning cooler" : live?.direction === "warmer" ? "leaning warmer" : "no change needed";
  const pct = live ? Math.min(100, Math.round((Math.abs(live.score) / Math.max(0.01, live.threshold)) * 100)) : 0;

  return <aside className="glance" aria-label="Tonight at a glance">
    <div className="k">Right now</div>
    <div className="card gl-grid">
      <Stat k="Bed" v={me.bed.measured_c != null ? `${tempNum(me.bed.measured_c, u)}°` : "–"}
        sub={bedVsPlan == null ? undefined : Math.abs(bedVsPlan) < 0.3 ? "on plan" : `${bedVsPlan > 0 ? "+" : "−"}${u === "F" ? Math.round(Math.abs(bedVsPlan) * 9 / 5) : Math.abs(bedVsPlan).toFixed(1)}° vs plan`} />
      <Stat k="In bed" v={me.presence.present && n.in_bed ? mins(snap.now - n.in_bed) : me.presence.present ? "yes" : "no"} sub={n.phase_name ?? STATE_TEXT[n.state] ?? n.state} />
      <Stat k="Heart rate" v={me.presence.hr ? `${me.presence.hr}` : "–"} sub={me.presence.hr ? "bpm" : "no reading"} />
      <Stat k="Breathing" v={me.presence.br ? `${me.presence.br}` : "–"} sub={me.presence.br ? "per min" : "no reading"} />
      <Stat k="Room" v={sig.room_c != null ? `${tempNum(sig.room_c as number, u)}°` : "–"} sub={sig.humidity != null ? `${sig.humidity}% humidity` : "no sensor"} />
      <Stat k="Next" v={me.next ? (me.next.temp_c != null ? `${tempNum(me.next.temp_c, u)}°` : "off") : "–"} sub={me.next ? `${me.next.name} · ${clock(me.next.at, snap.tz)}` : undefined} />
    </div>

    {partner && <>
      <div className="k">{partner.name}</div>
      <div className="card gl-partner">
        <span className="big num" style={{ fontSize: 40, color: personaColor(partner) }}>
          {partner.night.target_c != null ? <>{tempNum(partner.night.target_c, u)}<span className="deg">°</span></> : <span style={{ fontSize: 26 }}>Off</span>}</span>
        <span><span>{partner.night.phase_name && partner.night.state === "rhythm" ? partner.night.phase_name : STATE_TEXT[partner.night.state] ?? partner.night.state}</span>
          <small className="mute" style={{ display: "block" }}>{partner.presence.present ? "In bed" : partner.presence.present === false ? "Not in bed" : "Presence unknown"}
            {partner.night.flash ? ` · ${partner.night.flash.kind} flash` : ""}</small></span>
      </div>
    </>}

    {last && <>
      <div className="k">Last night · {last.label}</div>
      <div className="card gl-grid">
        <Stat k="In bed" v={last.in_bed_min != null ? `${Math.floor(last.in_bed_min / 60)} h ${last.in_bed_min % 60} min` : "–"}
          sub={last.time_to_settle_min != null ? `settled in ${last.time_to_settle_min} min` : undefined} />
        <Stat k="Times up" v={`${last.times_up ?? 0}`} sub={last.hr_avg ? `heart rate ${last.hr_avg} bpm on average` : undefined} />
        <Stat k="Flashes" v={`${(last.hot_flashes ?? 0) + (last.cold_flashes ?? 0)}`} sub={`${last.adjustments ?? 0} temperature change${last.adjustments === 1 ? "" : "s"}`} />
        <Stat k="Felt" v={last.feedback?.rested ? `${last.feedback.rested}/5` : "–"} sub={last.feedback?.temp ? last.feedback.temp.replace("_", " ") : "no check-in"} />
      </div>
    </>}

    <div className="k">Autopilot</div>
    <div className="card gl-ap">
      {!ap ? <span className="mute">Loading…</span> : ap.policy.mode === "off" ? <span className="mute">Off. Turn it on in Settings or Studio → Autopilot.</span> : <>
        <div className="row"><span>{ap.policy.mode === "act" ? "Adjusting within your limits" : "Suggesting only"}</span>
          <span className="mute" style={{ fontSize: 13 }}>{live ? lean : "starts once you're in bed"}</span></div>
        {live && <>
          <div className="gl-bar" title={`${pct}% of the way to acting`}><i style={{ width: `${pct}%` }} /></div>
          {live.reasons.slice(0, 3).map(r => <div key={r.signal} className="row" style={{ fontSize: 13 }}>
            <span>{r.label}<small className="mute" style={{ display: "block" }}>{r.detail}</small></span><span className="num mute">{inUnits(r.value, u)}</span></div>)}
          {!live.reasons.length && <span className="mute" style={{ fontSize: 13 }}>Nothing pushing either way.</span>}
        </>}
        {!ap.has_skin && <small className="faint">No skin temperature yet. Connect a watch or ring for better calls.</small>}
      </>}
    </div>
  </aside>;
}
