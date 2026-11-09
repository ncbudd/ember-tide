/** What the ring (or another wearable with its own nightly verdict) said about a night: readiness, its sleep score,
 *  temperature vs normal, HRV, lowest heart rate and breathing, each next to the bed's own number where it has one,
 *  and a month of temperature so a slow cycle or a sudden spike is easy to see. Server: engine/ring.py. */

export interface Ring {
  device: string; unusual: boolean;
  lines: { key: string; label: string; value: string; note: string; tone: "good" | "warn" | null; c?: number; bed?: number | null }[];
  trend: { date: string; c: number }[];
}

const toneColor = { good: "var(--good)", warn: "var(--warn)" } as const;

export default function RingCard({ ring, units, date }: { ring: Ring; units: "F" | "C"; date?: string }) {
  const temp = (txt: string, c?: number) =>
    c == null || units === "C" ? txt : `${c >= 0 ? "+" : "−"}${Math.abs(c * 1.8).toFixed(1)} °F`;
  return (
    <div className="card ringcard">
      <span className="k">From your {ring.device === "Oura" ? "Oura ring" : ring.device}</span>
      <div className="list">
        {ring.lines.map(l => (
          <div className="row" key={l.key} style={{ alignItems: "flex-start" }}>
            <span style={{ minWidth: 0 }}>{l.label}{l.note && <><br /><small className="mute">{l.note}</small></>}</span>
            <span className="num" style={{ fontSize: 17, whiteSpace: "nowrap", color: l.tone ? toneColor[l.tone] : undefined }}>
              {l.key === "temp_dev" ? temp(l.value, l.c) : l.value}</span>
          </div>
        ))}
      </div>
      {ring.trend.length >= 5 && <TempTrend trend={ring.trend} units={units} date={date} />}
    </div>
  );
}

/** A month of "temperature vs your normal": one dot per night, the zero line, and ±0.5 °C shaded as "about normal". */
function TempTrend({ trend, units, date }: { trend: Ring["trend"]; units: "F" | "C"; date?: string }) {
  const W = 300, H = 90, pad = 6;
  const lim = Math.max(1, ...trend.map(t => Math.abs(t.c)));
  const x = (i: number) => pad + (i * (W - 2 * pad)) / Math.max(1, trend.length - 1);
  const y = (c: number) => H / 2 - (c / lim) * (H / 2 - pad);
  const pts = trend.map((t, i) => `${x(i).toFixed(1)},${y(t.c).toFixed(1)}`).join(" ");
  const k = units === "F" ? 1.8 : 1;
  const fmt = (c: number) => `${c >= 0 ? "+" : "−"}${Math.abs(c * k).toFixed(1)}°`;
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <span style={{ fontSize: 13.5 }}>Temperature, last {trend.length} nights</span>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: 520, height: "auto", display: "block" }} role="img"
        aria-label={`Temperature compared with your normal, from ${fmt(Math.min(...trend.map(t => t.c)))} to ${fmt(Math.max(...trend.map(t => t.c)))}`}>
        <rect x={0} y={y(0.5)} width={W} height={y(-0.5) - y(0.5)} fill="var(--good)" opacity={0.1} />
        <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="var(--line2)" strokeDasharray="3 3" />
        <polyline points={pts} fill="none" stroke="var(--acc)" strokeWidth={1.5} strokeLinejoin="round" opacity={0.6} />
        {trend.map((t, i) => <circle key={t.date} cx={x(i)} cy={y(t.c)} r={t.date === date ? 4.5 : 2.6}
          fill={t.c >= 1 ? "var(--warn)" : "var(--acc)"} stroke={t.date === date ? "var(--text)" : "none"} strokeWidth={1.5}>
          <title>{t.date}: {fmt(t.c)}</title></circle>)}
      </svg>
      <small className="mute">Compared with your normal (dashed line); the shaded band is about normal. A slow rise and fall over a few weeks is usually your cycle; a sudden spike is usually illness, alcohol or a late meal.</small>
    </div>
  );
}
