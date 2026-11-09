import { useEffect, useState } from "react";
import { api } from "../api";

/** Naps from the person's watch or ring (engine/sleeps.py): on the day before this night, where they happened, and
 *  the day's whole sleep. Sleep screen and Studio's night view. `Sleep14` is a two-week bar of night + naps. */

export interface Nap {
  id: string; kind: "nap" | "night"; start: number; end: number; when: string; line: string;
  asleep_min: number | null; where: "bed" | "away" | "unknown" | null; where_why?: string | null; hr_avg?: number | null; device?: string;
}

const fmt = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} h ${Math.round(m % 60)} min` : `${Math.round(m)} min`);

export default function NapsCard({ naps, nightMin }: { naps: Nap[]; nightMin?: number | null }) {
  if (!naps.length) return null;
  const napMin = naps.reduce((a, n) => a + (n.asleep_min ?? 0), 0);
  return (
    <div className="card">
      <span className="k">{naps.length === 1 ? "Nap that day" : `${naps.length} naps that day`}</span>
      <div className="list">
        {naps.map(n => (
          <div className="row" key={n.id} style={{ alignItems: "flex-start" }}>
            <span style={{ minWidth: 0 }}>{n.when}<br /><small className="mute">{n.where_why ?? (n.device ? `From ${n.device}` : "")}</small></span>
            <span style={{ textAlign: "right", fontSize: 14 }}>{n.asleep_min ? fmt(n.asleep_min) : "—"}<br />
              <small className="mute">{n.where === "bed" ? "on the bed" : n.where === "away" ? "away from the bed" : ""}</small></span>
          </div>
        ))}
      </div>
      {nightMin ? <span className="mute" style={{ fontSize: 13 }}>Asleep that day and night: {fmt(nightMin + napMin)} ({fmt(nightMin)} night + {fmt(napMin)} {naps.length === 1 ? "nap" : "naps"})</span>
        : <span className="mute" style={{ fontSize: 13 }}>A long or late nap can make it harder to fall asleep that night.</span>}
    </div>
  );
}

/** Two weeks of sleep per day: the night and the naps stacked, with the person's own average as a line. */
export function Sleep14({ person }: { person: string }) {
  const [days, setDays] = useState<{ date: string; night_min: number; nap_min: number; naps: number; total_min: number }[] | null>(null);
  useEffect(() => { api.get<{ days: typeof days }>(`/api/people/${person}/sleeps?days=14`).then(r => setDays(r.days)).catch(() => setDays(null)); }, [person]);
  if (!days || days.length < 2) return null;
  const max = Math.max(9 * 60, ...days.map(d => d.total_min));
  const avg = days.reduce((a, d) => a + d.total_min, 0) / days.length;
  const W = 320, H = 110, bw = W / days.length;
  const y = (m: number) => H - (m / max) * (H - 14);
  const naps = days.reduce((a, d) => a + d.naps, 0);
  return (
    <div className="card">
      <span className="k">Sleep, last {days.length} days</span>
      <svg viewBox={`0 0 ${W} ${H + 14}`} width="100%" style={{ maxWidth: 560, height: "auto", display: "block" }} role="img"
        aria-label={`Average ${fmt(avg)} a day including naps`}>
        {days.map((d, i) => (
          <g key={d.date}>
            <rect x={i * bw + 2} y={y(d.night_min)} width={bw - 4} height={H - y(d.night_min)} rx={2} fill="var(--acc)" opacity={0.75}>
              <title>{d.date}: night {fmt(d.night_min)}</title></rect>
            {d.nap_min > 0 && <rect x={i * bw + 2} y={y(d.total_min)} width={bw - 4} height={y(d.night_min) - y(d.total_min)} rx={2} fill="var(--warm)">
              <title>{d.date}: naps {fmt(d.nap_min)}</title></rect>}
            <text x={i * bw + bw / 2} y={H + 11} fontSize={8.5} textAnchor="middle" fill="var(--mute)">
              {new Date(d.date + "T12:00").toLocaleDateString([], { weekday: "narrow" })}</text>
          </g>
        ))}
        <line x1={0} x2={W} y1={y(avg)} y2={y(avg)} stroke="var(--text)" strokeDasharray="3 3" opacity={0.5} />
      </svg>
      <span className="mute" style={{ fontSize: 13 }}>
        <span style={{ color: "var(--acc)" }}>■</span> night <span style={{ color: "var(--warm)" }}>■</span> naps · dashed: your average, {fmt(avg)} a day
        {naps ? ` · ${naps} nap${naps === 1 ? "" : "s"}` : ""}</span>
    </div>
  );
}
