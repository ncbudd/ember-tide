import { useEffect, useState } from "react";
import { api } from "../api";
import SleepStageBar, { STAGE_COLORS, STAGE_ORDER, stageLabel, stageTotal, type SleepStage, type StageTotals } from "./SleepStageBar";

interface HNight {
  date: string; label: string; in_progress: boolean; has_data: boolean; away?: boolean; in_bed_min?: number; time_to_settle_min?: number | null;
  times_up?: number; feedback: { temp?: string } | null; reviewed?: boolean; applied?: number;
  stage_totals?: StageTotals | null; stage_totals_from?: string | null;
}

const FEEL: Record<string, [string, string]> = { cold: ["Too cold", "tide"], right: ["Just right", "good"], warm: ["Too warm", "ember"] };
type Filter = "all" | "applied" | "feel";

const hm = (min?: number) => (min == null ? "—" : `${Math.floor(min / 60)} h ${String(Math.round(min % 60)).padStart(2, "0")}`);

/** Sleep → History: the last 30 nights, newest first, each with its check-in, whether it was reviewed and what was
 *  applied from it. Tapping a night opens its review. */
export default function SleepHistory({ pid, onOpen }: { pid: string; onOpen: (date: string) => void }) {
  const [nights, setNights] = useState<HNight[] | null>(null);
  const [f, setF] = useState<Filter>("all");
  useEffect(() => { api.get<HNight[]>(`/api/people/${pid}/nights?count=30`).then(setNights).catch(() => setNights([])); }, [pid]);
  if (!nights) return <p className="mute">Loading…</p>;
  const done = nights.filter(n => !n.in_progress);
  const week = done.slice(0, 7).filter(n => n.has_data && n.in_bed_min && !n.away);
  const avg = week.length ? week.reduce((a, n) => a + (n.in_bed_min ?? 0), 0) / week.length : null;
  const stageNights = done.filter(n => !n.away).slice(0, 14).reverse();
  const maxStage = Math.max(1, ...stageNights.map(n => stageTotal(n.stage_totals)));
  const hasStageTotals = stageNights.some(n => stageTotal(n.stage_totals) > 0);
  const stageDevices = [...new Set(stageNights.filter(n => stageTotal(n.stage_totals) > 0).map(n => n.stage_totals_from).filter((x): x is string => !!x))];
  const stageFrom = stageDevices.length === 1 ? stageDevices[0] : null;
  const chartTitle = stageFrom ? `${stageFrom} stage totals` : "Stage totals";
  const shown = done.filter(n => f === "all" || (f === "applied" && (n.applied ?? 0) > 0) || (f === "feel" && !!n.feedback?.temp));
  const filters: [Filter, string][] = [["all", "All nights"], ["feel", "Checked in"], ["applied", "With changes"]];
  return (
    <div className="shist">
      {avg != null && <p className="mute" style={{ margin: 0 }}>Last {week.length} nights: {hm(avg)} in bed on average.</p>}
      {hasStageTotals && <div className="card stage-history-chart">
        <div className="row"><b>{chartTitle}</b><span className="mute">Last 14 nights</span></div>
        <div className="stage-history-bars" role="group" aria-label={`${chartTitle} by night`}>
          {stageNights.map(n => {
            const d = new Date(n.date + "T12:00:00");
            const totals = n.stage_totals ?? {};
            const title = stageTotal(totals) > 0 ? `${n.label} · ${stageLabel(totals, n.stage_totals_from)}` : `${n.label} · no stage totals`;
            return <div key={n.date} className="stage-history-night" title={title}>
              <span className="stage-history-track"><SleepStageBar totals={n.stage_totals} from={n.stage_totals_from} vertical scale={maxStage} /></span>
              <small>{d.getDate()}</small>
            </div>;
          })}
        </div>
        <div className="stagekey">
          {STAGE_ORDER.map((stage: SleepStage) => <span key={stage}><i style={{ backgroundColor: STAGE_COLORS[stage] }} />{stage === "rem" ? "REM" : stage[0].toUpperCase() + stage.slice(1)}</span>)}
        </div>
        <p className="stage-history-note">Nightly duration totals only; {stageFrom ? `${stageFrom} does` : "these devices do"} not provide stage timing for this chart.</p>
      </div>}
      <div className="chips" role="group" aria-label="Show">
        {filters.map(([id, l]) => <button key={id} className="chip" aria-pressed={f === id} onClick={() => setF(id)}>{l}</button>)}
      </div>
      <div className="card" style={{ gap: 0, padding: "4px 12px" }}>
        {shown.length === 0 && <p className="mute">No nights here yet.</p>}
        {shown.map(n => { const d = new Date(n.date + "T12:00:00"); const fl = n.feedback?.temp ? FEEL[n.feedback.temp] : null;
          const has = n.has_data && !!n.in_bed_min && !n.away;     // (a guest night, or nobody seen on the side: nothing to open)
          return (
          <button key={n.date} className="shist-row" onClick={() => onOpen(n.date)} disabled={!has}>
            <span className="shist-d"><b>{d.getDate()}</b><span>{d.toLocaleDateString([], { weekday: "short" })}</span></span>
            <span className="shist-mid">
              <span>{n.away ? "Away" : !has ? "Nothing recorded" : `${hm(n.in_bed_min)} in bed${n.time_to_settle_min != null ? ` · settled ${n.time_to_settle_min} min` : ""}${n.times_up ? ` · up ${n.times_up}×` : ""}`}</span>
              <span className="shist-tags">
                {fl && <span className={`shist-tag ${fl[1]}`}>{fl[0]}</span>}
                {n.reviewed && <span className="shist-tag">Reviewed</span>}
                {(n.applied ?? 0) > 0 && <span className="shist-tag good">{n.applied} applied</span>}
              </span>
            </span>
            {has && <span aria-hidden="true" className="faint">›</span>}
          </button>); })}
      </div>
    </div>
  );
}
