import { useEffect, useId, useRef, useState } from "react";
import type { PersonView } from "../types";
import { clock, cToF, fToC, personaColor, h12 } from "../format";

interface Props {
  people: PersonView[];
  now: number;
  tz: string;
  units: "F" | "C";
  height?: number;
  /** drawn at its real pixel width (Tonight): keep text and margins at their normal size instead of scaling with width */
  realSize?: boolean;
  gradient?: boolean;          // color the line by temperature (single-person view)
  showActual?: boolean;
  compact?: boolean;
  width?: number;
  fill?: boolean;              // stretch vertically to fill the parent (keeps text undistorted)
}

function smooth(p: [number, number][]): string {
  if (p.length < 2) return "";
  let d = `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}`;
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[i - 1] || p[i], p1 = p[i], p2 = p[i + 1], p3 = p[i + 2] || p2;
    d += ` C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)},${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)} ${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)},${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}
const line = (p: [number, number][]) => (p.length ? "M" + p.map(q => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(" L") : "");
function interp(pts: [number, number][], t: number): number | null {
  if (!pts.length || t < pts[0][0] || t > pts[pts.length - 1][0]) return null;
  let lo = 0, hi = pts.length - 1;
  while (lo < hi - 1) { const m = (lo + hi) >> 1; if (pts[m][0] <= t) lo = m; else hi = m; }
  const a = pts[lo], b = pts[hi];
  return b[0] === a[0] ? a[1] : a[1] + ((b[1] - a[1]) * (t - a[0])) / (b[0] - a[0]);
}
function nearest(pts: [number, number][], t: number, maxGap: number): number | null {
  let best: [number, number] | null = null;
  for (const p of pts) if (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
  return best && Math.abs(best[0] - t) <= maxGap ? best[1] : null;
}

export default function Horizon({ people, now, tz, units, height = 150, gradient = false, showActual = true, compact = false, width = 340, fill = false, realSize = false }: Props) {
  const gid = useId().replace(/:/g, "");
  const box = useRef<HTMLDivElement>(null);
  const [fillH, setFillH] = useState<number | null>(null);
  const [scrub, setScrub] = useState<{ t: number; px: number; mouse: boolean } | null>(null);
  const hideTimer = useRef<number>();
  useEffect(() => {
    if (!fill || !box.current) return;
    const el = box.current;
    // only real changes (> 2 px): the chart sits in this box, so re-measuring after every redraw could creep forever
    const ro = new ResizeObserver(() => { const r = el.getBoundingClientRect(); if (r.width > 0 && r.height > 0) { const h = Math.max(120, Math.min(420, (r.height / r.width) * width)); setFillH(c => (c === null || Math.abs(c - h) > 2 ? h : c)); } });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fill, width]);
  if (fill) height = fillH ?? height;

  const grow = realSize ? 0 : Math.max(0, width - 340) / 40;
  const W = width, H = height, L = 6, R = 6, T = 16 + grow, B = compact ? 6 : 20 + grow;
  const withCurve = people.filter(p => p.curve.length > 1 && p.schedule);
  if (!withCurve.length) {
    return <div ref={box}><svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="No rhythm scheduled" style={{ width: "100%" }}>
      <text x={W / 2} y={H / 2} textAnchor="middle" fill="var(--mute)" fontSize="12">No rhythm tonight</text></svg></div>;
  }
  const x0 = Math.min(...withCurve.map(p => Math.min(p.schedule!.precool, p.curve[0][0]))) - 900;
  const x1 = Math.max(...withCurve.map(p => p.schedule!.end)) + 600;
  const vals = withCurve.flatMap(p => [...p.curve.map(c => c[1]), ...(p.night.target_c != null ? [p.night.target_c] : [])]);
  const lo = Math.min(...vals) - 1, hi = Math.max(...vals) + 1;
  const X = (t: number) => L + ((t - x0) / (x1 - x0)) * (W - L - R);
  const Y = (v: number) => H - B - ((v - lo) / (hi - lo)) * (H - T - B);
  const color = (p: PersonView) => (personaColor(p));
  const nowX = X(Math.min(Math.max(now, x0), x1));
  const hours: number[] = [];
  const start = new Date(x0 * 1000); start.setMinutes(0, 0, 0);
  for (let t = start.getTime() / 1000 + 3600; t < x1; t += 3600) hours.push(t);
  const fmtH = (t: number) => new Date(t * 1000).toLocaleTimeString([], { ...h12(), hour: "numeric", timeZone: tz }).replace(" ", "").toLowerCase();
  const single = withCurve.length === 1;
  const fs = realSize ? 10 : Math.round(9 * Math.max(1, W / 480));
  const deg = (c: number, d = 0) => `${(units === "F" ? cToF(c) : c).toFixed(d)}°`;

  const toT = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    return { t: x0 + ((Math.min(W - R, Math.max(L, px)) - L) / (W - L - R)) * (x1 - x0), px: clientX - r.left };
  };
  const onMove = (e: React.PointerEvent) => { clearTimeout(hideTimer.current); setScrub({ ...toT(e.clientX), mouse: e.pointerType === "mouse" }); };
  const onEnd = (e: React.PointerEvent) => { clearTimeout(hideTimer.current); hideTimer.current = window.setTimeout(() => setScrub(null), e.pointerType === "mouse" ? 0 : 1800); };

  const rows = scrub ? withCurve.map(p => {
    const plan = interp(p.curve, scrub.t);
    const usual = p.rhythm_curve ? interp(p.rhythm_curve, scrub.t) : null;
    const bed = showActual ? nearest(p.actual, scrub.t, 600) : null;
    const ph = [...p.phases].reverse().find(x => x.start <= scrub.t);
    const changed = usual != null && plan != null && Math.abs(usual - plan) > 0.2;
    const fl = p.night.flash && scrub.t >= p.night.flash.start && scrub.t < p.night.flash.ease_end ? (p.night.flash.kind === "hot" ? "hot flash" : "cold flash") : null;
    return { p, plan, usual: changed ? usual : null, why: fl ?? (changed ? "your change" : null), bed: scrub.t > now ? null : bed, phase: ph?.name ?? (scrub.t < (p.phases[0]?.start ?? 0) ? "Before wind-down" : null) };
  }) : [];
  const boxW = box.current?.clientWidth ?? 340;

  return (
    <div ref={box} style={{ position: "relative", ...(fill ? { flex: 1, alignSelf: "stretch", minHeight: 0, display: "flex", alignItems: "center" } : {}) }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block", touchAction: "pan-y", cursor: "crosshair" }} role="img"
        aria-label={`Tonight's temperature rhythm for ${withCurve.map(p => p.name).join(" and ")}. Slide along it to see values.`}
        onPointerDown={onMove} onPointerMove={onMove} onPointerUp={onEnd} onPointerLeave={onEnd} onPointerCancel={onEnd}>
        <defs>
          {/* fixed temperatures, not the chart's range: 64°F and below reads cool, 78°F and above reads warm,
              so a warm sleeper's 72° line isn't drawn in the cool colour just because it's their lowest point */}
          <linearGradient id={`g${gid}`} gradientUnits="userSpaceOnUse" x1="0" y1={Y(fToC(64))} x2="0" y2={Y(fToC(78))}>
            <stop offset="0" stopColor="var(--cool)" />
            <stop offset="1" stopColor="var(--warm)" />
          </linearGradient>
        </defs>
        {!compact && hours.filter((_, i) => i % 2 === 0).map(t => (
          <text key={t} x={X(t)} y={H - 5} textAnchor="middle" fontSize={fs} fill="var(--mute)">{fmtH(t)}</text>
        ))}
        {withCurve.map(p => {
          const pts = p.curve.map(([t, v]) => [X(t), Y(v)] as [number, number]);
          const past = pts.filter((_, i) => p.curve[i][0] <= now);
          const fut = pts.filter((_, i) => p.curve[i][0] >= now);
          if (past.length && fut.length) past.push(fut[0]);
          const stroke = gradient && single ? `url(#g${gid})` : color(p);
          const act = showActual ? p.actual.filter(a => a[0] >= x0).map(([t, v]) => [X(t), Y(v)] as [number, number]) : [];
          const rc = single && p.rhythm_curve ? p.rhythm_curve.filter(c => c[0] >= now).map(([t, v]) => [X(t), Y(v)] as [number, number]) : [];
          return (
            <g key={p.id}>
              {act.length > 1 && <path d={line(act)} fill="none" stroke="var(--text)" strokeOpacity=".3" strokeWidth="1" />}
              {rc.length > 1 && <path d={smooth(rc)} fill="none" stroke="var(--mute)" strokeOpacity=".55" strokeWidth="1.2" strokeDasharray="1 3" />}
              <path d={smooth(past)} fill="none" stroke={stroke} strokeWidth={single ? 3 : 2.6} strokeLinecap="round" />
              <path d={smooth(fut)} fill="none" stroke={stroke} strokeWidth={single ? 2 : 1.6} strokeDasharray="3 4" strokeOpacity=".8" />
              {p.night.in_bed && p.night.in_bed >= x0 && (
                <g>
                  <line x1={X(p.night.in_bed)} x2={X(p.night.in_bed)} y1={T} y2={H - B} stroke={color(p)} strokeOpacity=".45" />
                  {single && !compact && <text x={X(p.night.in_bed) + 3} y={T + fs} fontSize={fs} fill={color(p)}>in bed</text>}
                </g>
              )}
              {p.night.flash && (
                <text x={X(p.night.flash.start)} y={T - 4} textAnchor="middle" fontSize={fs + 2} fill={p.night.flash.kind === "hot" ? "var(--cool)" : "var(--warm)"}>
                  {p.night.flash.kind === "hot" ? "❄" : "☼"}</text>
              )}
            </g>
          );
        })}
        <line x1={nowX} x2={nowX} y1={4} y2={H - B} stroke="var(--text)" strokeOpacity=".25" strokeDasharray="2 3" />
        {withCurve.map(p => {
          const c = p.night.target_c;
          if (c == null) return null;
          return (
            <g key={p.id + "n"}>
              <circle cx={nowX} cy={Y(Math.min(hi, Math.max(lo, c)))} r="9" fill={color(p)} fillOpacity=".15" />
              <circle className="breathe" cx={nowX} cy={Y(Math.min(hi, Math.max(lo, c)))} r="4.5" fill={single ? "var(--text)" : color(p)} />
            </g>
          );
        })}
        {scrub && <>
          <line x1={X(scrub.t)} x2={X(scrub.t)} y1={2} y2={H - B} stroke="var(--text)" strokeOpacity=".5" />
          {rows.map(r => r.plan != null && <circle key={r.p.id} cx={X(scrub.t)} cy={Y(r.plan)} r="4" fill="var(--bg)" stroke={color(r.p)} strokeWidth="2" />)}
          {rows.map(r => r.bed != null && <circle key={r.p.id + "b"} cx={X(scrub.t)} cy={Y(r.bed)} r="2.5" fill="var(--text)" />)}
        </>}
      </svg>
      {scrub && (
        <div className="scrubtip" style={scrub.mouse
          ? { left: scrub.px + 14 + 180 <= boxW ? scrub.px + 14 : Math.max(0, scrub.px - 14 - 180), top: 8 }
          : { left: Math.max(4, Math.min(boxW - 184, scrub.px - 90)), top: -64 }}>
          <div className="row"><b>{clock(scrub.t, tz)}</b><span className="mute">{scrub.t > now + 60 ? "planned" : scrub.t < now - 60 ? "earlier" : "now"}</span></div>
          {rows.map(r => (
            <div key={r.p.id} className="srow">
              {!single && <span style={{ color: color(r.p) }}>{r.p.name}</span>}
              {r.phase && <span className="mute">{r.phase}</span>}
              <span>plan <b>{r.plan != null ? deg(r.plan) : "off"}</b>{r.bed != null && <> · bed <b>{deg(r.bed, 1)}</b></>}</span>
              {r.usual != null && <span className="mute">{r.why}: usually {deg(r.usual)}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
