import { useEffect, useMemo, useRef, useState } from "react";
import NightFix from "../components/NightFix";
import RingCard, { type Ring } from "../components/RingCard";
import NapsCard, { type Nap, Sleep14 } from "../components/NapsCard";
import PetQuestions from "./PetQuestions";
import { api } from "../api";
import ShowMore from "../components/ShowMore";
import { useApp } from "../ctx";
import { clock, cToF, sourceText, weight, weightUnit } from "../format";

export interface Series { id: string; label: string; source: string; device: string; driver: string; unit: string; lane: string; kind: "line" | "step"; color: string; points: [number, number][]; role?: string; ghost?: boolean }
interface Moment { kind: string; ts: number; end?: number; count?: number; delta_c?: number; temp_c?: number | null; minutes?: number; extended?: number; source?: string | null; home?: boolean | null; why?: string; text?: string }
interface StageSrc { id: string; label: string; kind: string; segments: { start: number; end: number; stage: string }[]; totals: Record<string, number>; agree_with_first?: number | null }
const STAGE_COLOR: Record<string, string> = { awake: "#e3b37a", rem: "#a08be2", light: "#7fb6cc", deep: "#3f6fb5" };
const STAGE_LV = ["awake", "rem", "light", "deep"];
export interface Explore { stages: StageSrc[]; date: string; start: number; end: number; now: number; tz: string; person: string; zone: string; series: Series[]; moments: Moment[]; phases: { id: string; name: string; start: number; temp_c: number }[]; schedule: { bedtime: number; wake: number; window_start: number; window_end: number } | null }

const LANES: { id: string; label: string; h: number }[] = [
  { id: "temp", label: "Temperature", h: 210 }, { id: "body", label: "Heart & breathing", h: 120 }, { id: "skin", label: "Skin temperature", h: 80 },
  { id: "presence", label: "In bed", h: 0 }, { id: "env", label: "Room", h: 90 }, { id: "context", label: "Context", h: 0 },
  { id: "raw", label: "Unfiltered sensor readings", h: 70 },
];
// context rows: phone at home, distance from home, calendar, bedroom light (colours come from the server)
const ctxH = (role?: string) => (role === "location" ? 44 : 22);
const DEFAULT_OFF = new Set(["setpoint", "status", "br"]);
/** Where a series comes from, in plain words ("sim" and "ha-entity" mean nothing to most people). */
function driverName(d: string): string {
  return ({ sim: "simulated", "ha-entity": "Home Assistant", "mqtt-topic": "MQTT", engine: "Ember & Tide", ics: "calendar feed",
    "orion-mqtt": "bed (local)", "orion-cloud": "bed (cloud)" } as Record<string, string>)[d] ?? d;
}
function deviceLine(device: string, driver: string): string {
  const dn = driverName(driver);
  return device.toLowerCase().includes(dn.toLowerCase().split(" ")[0]) ? device : `${device} · ${dn}`;
}
const MOMENT_GLYPH: Record<string, string> = { hot_flash: "❄", cold_flash: "☼", adjust: "±", rhythm_started: "◐", out_of_bed: "○", tonight_skip: "○", tonight_pause: "‖", rhythm_changed: "✎", adjust_cleared: "↺", tonight_start_now: "▸", home_changed: "⌂", pet: "∴", active: "·" };
function momentLabel(m: Moment): string {
  if (m.kind === "home_changed") return `${m.home === false ? "Left home" : m.home === true ? "Back home" : "Home or away not known"}${m.why ? ` (${m.why})` : ""}`;
  if (m.kind === "pet" || m.kind === "active" || m.kind === "room_cold") return m.text ?? (m.kind === "pet" ? "Looked like a pet" : m.kind === "active" ? "Active, not sleeping yet" : "The room was cold");
  return MOMENT_TEXT[m.kind] ?? m.kind.replace(/_/g, " ");
}
const MOMENT_TEXT: Record<string, string> = { hot_flash: "Hot flash", cold_flash: "Cold flash", adjust: "Manual change", rhythm_started: "Rhythm started (in bed)", out_of_bed: "Out of bed", tonight_skip: "Turned off for the night", tonight_pause: "Paused", rhythm_changed: "Rhythm edited", adjust_cleared: "Back to rhythm", tonight_start_now: "Started manually", home_changed: "Home / away" };

/** How long a silence still counts as "the same line": 3x the series' usual reporting interval, at least 5 min.
 *  Sensors that report every 10-15 minutes (many room sensors) would otherwise never draw a line. */
const gapCache = new WeakMap<[number, number][], number>();
function gapFor(pts: [number, number][]): number {
  const hit = gapCache.get(pts);
  if (hit !== undefined) return hit;
  const d: number[] = [];
  for (let i = 1; i < pts.length; i++) d.push(pts[i][0] - pts[i - 1][0]);
  d.sort((a, b) => a - b);
  const g = Math.max(300, (d.length ? d[d.length >> 1] : 0) * 3);
  gapCache.set(pts, g);
  return g;
}

function valueAt(pts: [number, number][], t: number, step: boolean, maxGap = Math.max(120, gapFor(pts) / 2)): number | null {
  if (!pts.length) return null;
  let lo = 0, hi = pts.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (pts[mid][0] <= t) lo = mid; else hi = mid - 1; }
  const a = pts[lo];
  if (step) return a[0] <= t && t - a[0] <= maxGap * 4 ? a[1] : null;
  const b = pts[lo + 1];
  if (a[0] <= t && b && b[0] - a[0] <= maxGap * 2) return a[1] + ((b[1] - a[1]) * (t - a[0])) / (b[0] - a[0]);
  return Math.abs(a[0] - t) <= maxGap ? a[1] : null;
}

export default function Explorer() {
  const { snap } = useApp();
  const [pid, setPid] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [cmpDate, setCmpDate] = useState<string>("");
  const [data, setData] = useState<Explore | null>(null);
  const [cmp, setCmp] = useState<Explore | null>(null);
  const [nights, setNights] = useState<{ date: string; label: string; has_data: boolean; in_progress: boolean }[]>([]);
  const [off, setOff] = useState<Set<string>>(new Set(DEFAULT_OFF));
  const [showRaw, setShowRaw] = useState(false);
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [hover, setHover] = useState<{ t: number; x: number; y: number } | null>(null);
  const [drag, setDragState] = useState<{ x0: number; x1: number } | null>(null);
  const dragRef = useRef<{ x0: number; x1: number } | null>(null);
  const setDrag = (d: { x0: number; x1: number } | null | ((p: { x0: number; x1: number } | null) => { x0: number; x1: number } | null)) => {
    const next = typeof d === "function" ? d(dragRef.current) : d; dragRef.current = next; setDragState(next);
  };
  const [w, setW] = useState(900);
  const wrap = useRef<HTMLDivElement>(null);
  const pathCache = useRef(new Map<string, string>());
  const raf = useRef<number | null>(null);
  const person = snap?.people.find(p => p.id === (pid ?? snap.people[0]?.id));
  const units = person?.settings.units ?? "F";
  const wu = weightUnit(person?.settings);
  // refresh the open night about every 30 real seconds (not every simulated minute: at ×60 that hammered the server every second)
  const [bump, setBump] = useState(0);
  const [pets, setPets] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => { api.get<{ pets: { id: string; name: string }[] }>("/api/pets").then(r => setPets(r.pets)).catch(() => setPets([])); }, []);
  const tick = (snap ? Math.floor(Date.now() / 30000) : 0) + bump * 1e9;

  useEffect(() => { if (!wrap.current) return; const ro = new ResizeObserver(() => setW(Math.max(600, wrap.current!.clientWidth))); ro.observe(wrap.current); return () => ro.disconnect(); }, []);
  useEffect(() => { if (person) api.get<(typeof nights[number] & { in_bed_min?: number })[]>(`/api/people/${person.id}/nights?count=14`).then(n => { setNights(n);
        const pick = n[0] && (!n[0].in_progress || (n[0].in_bed_min ?? 0) >= 60) ? n[0] : n.find(x => x.has_data && !x.in_progress) ?? n[0];
        setDate(d => d ?? pick?.date ?? null); }).catch(() => {}); }, [person?.id]);
  useEffect(() => { if (person && date) api.get<Explore>(`/api/explore?person_id=${person.id}&date=${date}`).then(setData).catch(() => setData(null)); }, [person?.id, date, tick]);
  const [ring, setRing] = useState<Ring | null>(null);         // the ring's own numbers for the night (engine/ring.py)
  const [naps, setNaps] = useState<{ list: Nap[]; night: number | null }>({ list: [], night: null });   // naps that day (engine/sleeps.py)
  useEffect(() => { setRing(null); setNaps({ list: [], night: null }); if (person && date) api.get<{ ring?: Ring | null; naps?: Nap[]; night_asleep_min?: number | null }>(`/api/people/${person.id}/night?date=${date}`)
    .then(r => { setRing(r.ring ?? null); setNaps({ list: r.naps ?? [], night: r.night_asleep_min ?? null }); }).catch(() => {}); }, [person?.id, date]);
  useEffect(() => { if (person && cmpDate) api.get<Explore>(`/api/explore?person_id=${person.id}&date=${cmpDate}`).then(setCmp).catch(() => setCmp(null)); else setCmp(null); }, [person?.id, cmpDate]);

  const conv = (s: Series, v: number) => (s.unit === "°C" ? (units === "F" ? cToF(v) : v) : v);
  const shift = data && cmp ? data.start - cmp.start : 0;
  const all = useMemo(() => {
    if (!data) return [] as (Series & { ghost?: boolean })[];
    const main = data.series.map(s => ({ ...s, lane: s.lane === "body" && s.unit === "°C" ? "skin" : s.role === "light" ? "context" : s.lane }));
    const ghosts = cmp ? cmp.series.filter(s => ["target", "measured", "hr"].includes(s.id)).map(s => ({ ...s, id: `cmp:${s.id}`, label: `${s.label} · ${cmp.date}`, ghost: true, points: s.points.map(([t, v]) => [t + shift, v] as [number, number]) })) : [];
    return [...main, ...ghosts];
  }, [data, cmp, shift]);
  if (!snap || !person) return null;
  if (!data) return <div className="box">Loading the night…</div>;

  const x0 = zoom ? zoom[0] : data.start, x1 = zoom ? zoom[1] : Math.min(data.end, Math.max(data.now, data.start + 3600));
  const L = 150, R = 16;
  const X = (t: number) => L + ((t - x0) / (x1 - x0)) * (w - L - R);
  const T = (px: number) => x0 + ((px - L) / (w - L - R)) * (x1 - x0);
  const visible = all.filter(s => !off.has(s.ghost ? s.id.slice(4) : s.id) && s.points.length && (s.lane !== "raw" || showRaw));
  const presenceSeries = visible.filter(s => s.lane === "presence");
  const contextSeries = visible.filter(s => s.lane === "context");
  const lanes = LANES.filter(l => (l.id !== "raw" || showRaw) && visible.some(s => s.lane === l.id)).map(l => (l.id === "presence" ? { ...l, h: Math.max(40, presenceSeries.length * 22 + 14) }
    : l.id === "context" ? { ...l, h: contextSeries.reduce((a, x) => a + ctxH(x.role), 0) + 10 } : l));
  const EV = 44, AX = 26;
  const stageSrcs = (data.stages ?? []).filter(x => !off.has(`stages:${x.id}`));
  if (stageSrcs.length) {
    const at = Math.max(0, lanes.findIndex(l => l.id === "body") + 1);
    lanes.splice(at, 0, { id: "stages", label: "Sleep stages", h: stageSrcs.length * 46 + 6 });
  }
  let y = EV;
  const laneBox = lanes.map(l => { const b = { ...l, y0: y, y1: y + l.h }; y += l.h + 22; return b; });
  const H = y + AX;

  const scaleFor = (laneId: string) => {
    const vs = visible.filter(s => s.lane === laneId).flatMap(s => s.points.filter(p => p[0] >= x0 && p[0] <= x1).map(p => conv(s, p[1])));
    if (!vs.length) return { lo: 0, hi: 1 };
    let lo = Infinity, hi = -Infinity;                   // a loop, not Math.min(...vs): huge spreads overflow the stack
    for (const v of vs) { if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
    if (!Number.isFinite(lo)) return { lo: 0, hi: 1 };
    if (hi - lo < 2) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.08;
    return { lo: lo - pad, hi: hi + pad };
  };
  const scales = Object.fromEntries(laneBox.map(l => [l.id, scaleFor(l.id)]));
  const Yf = (laneId: string, v: number) => { const b = laneBox.find(l => l.id === laneId); const s = scales[laneId]; if (!b || !s) return 0; return b.y1 - ((v - s.lo) / (s.hi - s.lo || 1)) * (b.y1 - b.y0); };

  const pathFor = (s: Series) => {
    const sc = scales[s.lane];
    const key = `${data.date}|${cmp?.date ?? ""}|${s.id}|${x0}|${x1}|${w}|${sc.lo.toFixed(3)}|${sc.hi.toFixed(3)}|${s.points.length}|${H}|${units}`;
    const hit = pathCache.current.get(key);
    if (hit !== undefined) return hit;
    if (pathCache.current.size > 400) pathCache.current.clear();
    let d = "", prevT = -1e18, prevY = 0;
    const gap = gapFor(s.points);
    for (const [t, v0] of s.points) {
      if (t < x0 - 600 || t > x1 + 600) continue;
      const px = X(t), py = Yf(s.lane, conv(s, v0));
      if (t - prevT > gap) d += `M${px.toFixed(1)},${py.toFixed(1)}h0.01`;
      else if (s.kind === "step") d += `L${px.toFixed(1)},${prevY.toFixed(1)}L${px.toFixed(1)},${py.toFixed(1)}`;
      else d += `L${px.toFixed(1)},${py.toFixed(1)}`;
      prevT = t; prevY = py;
    }
    pathCache.current.set(key, d);
    return d;
  };
  const presenceRows = (b: { y0: number }) => presenceSeries.map((s, i) => {
    const yy = b.y0 + 8 + i * 22;
    const runs: [number, number][] = []; let st: number | null = null, last = 0;
    const thr = s.role === "weight" ? 20 : 0.5, gap = gapFor(s.points);
    for (const [t, v] of s.points) { if (t < x0 - 600 || t > x1 + 600) continue; if (v >= thr && st == null) st = t; if ((v < thr || t - last > gap) && st != null) { runs.push([st, last]); st = v >= thr ? t : null; } last = t; }
    if (st != null) runs.push([st, last]);
    return <g key={s.id}><text x={L - 8} y={yy + 11} fontSize="11" textAnchor="end" fill="var(--mute)">{s.label.replace(/^In bed: /, "")}</text>
      <rect x={L} y={yy} width={w - L - R} height={14} rx={3} fill="var(--raise)" />
      {runs.map(([a, b2], k) => <rect key={k} x={X(Math.max(a, x0))} y={yy} width={Math.max(1, X(Math.min(b2, x1)) - X(Math.max(a, x0)))} height={14} rx={3} fill={s.color} opacity={s.ghost ? 0.35 : 0.85} />)}</g>;
  });

  const contextRows = (b: { y0: number }) => {
    let yy = b.y0 + 4;
    return contextSeries.map(s => {
      const y0 = yy; yy += ctxH(s.role);
      const col = s.color;
      const name = s.role === "home" ? "At home" : s.role === "location" ? "Distance" : s.role === "calendar" ? "Calendar" : s.role === "light" ? "Light" : s.label;
      const pts = s.points.filter(p => p[0] >= x0 - 3600 && p[0] <= x1 + 600);
      const label = <text x={L - 8} y={y0 + 11} fontSize="11" textAnchor="end" fill="var(--mute)">{name}</text>;
      if (s.role === "location") {
        const hi = Math.max(1, ...pts.map(p => p[1]));
        const Yl = (v: number) => y0 + 36 - (v / hi) * 32;
        let d = ""; let prev = -1e18; const gap = gapFor(s.points);
        for (const [t, v] of pts) { const px = X(Math.min(Math.max(t, x0), x1)); d += `${t - prev > gap ? "M" : "L"}${px.toFixed(1)},${Yl(v).toFixed(1)}`; prev = t; }
        return <g key={s.id}>{label}<text x={L - 8} y={y0 + 24} fontSize="9.5" textAnchor="end" fill="var(--faint)">up to {hi.toFixed(0)} km</text>
          <line x1={L} x2={w - R} y1={y0 + 36} y2={y0 + 36} stroke="var(--line)" /><path d={d} fill="none" stroke={col} strokeWidth={1.6} /></g>;
      }
      // spans: home / light = while on; calendar = from the reading until the event's end time
      const runs: [number, number][] = [];
      if (s.role === "calendar") {
        for (const [t, v] of pts) if (v > 0) { const last = runs[runs.length - 1]; if (last && t <= last[1] + 60) last[1] = Math.max(last[1], v); else runs.push([t, v]); }
      } else {
        let st: number | null = null, last = 0; const gap = gapFor(s.points);
        for (const [t, v] of pts) { if (v >= 0.5 && st == null) st = t; if ((v < 0.5 || t - last > gap) && st != null) { runs.push([st, last]); st = v >= 0.5 ? t : null; } last = t; }
        if (st != null) runs.push([st, last]);
      }
      return <g key={s.id}>{label}<rect x={L} y={y0} width={w - L - R} height={14} rx={3} fill="var(--raise)" />
        {runs.filter(([a, b2]) => b2 >= x0 && a <= x1).map(([a, b2], k) => <rect key={k} x={X(Math.max(a, x0))} y={y0} width={Math.max(1, X(Math.min(b2, x1)) - X(Math.max(a, x0)))} height={14} rx={3} fill={col} opacity={0.7} />)}</g>;
    });
  };

  const ticks: number[] = []; const span = x1 - x0; const stepS = span > 6 * 3600 ? 3600 : span > 2 * 3600 ? 1800 : span > 3600 ? 900 : 300;
  for (let t = Math.ceil(x0 / stepS) * stepS; t <= x1; t += stepS) ticks.push(t);
  const fmtT = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: stepS < 3600 ? "2-digit" : undefined, timeZone: data.tz });
  const phaseAt = (t: number) => [...data.phases].reverse().find(p => p.start <= t);
  const momentsIn = data.moments.filter(m => m.ts >= x0 && m.ts <= x1);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * w;
    const cx = e.clientX - r.left, cy = e.clientY - r.top;
    if (raf.current) cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(() => {
      raf.current = null;
      if (px < L || px > w - R) { setHover(null); return; }
      setHover({ t: T(px), x: cx, y: cy });
      if (drag) setDrag(d => (d ? { ...d, x1: px } : d));
    });
  };
  const onUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px1 = Math.min(w - R, Math.max(L, ((e.clientX - r.left) / r.width) * w));
    const d0 = dragRef.current;
    if (d0 && Math.abs(px1 - d0.x0) > 8) setZoom([T(Math.min(d0.x0, px1)), T(Math.max(d0.x0, px1))]);
    setDrag(null);
  };
  const tipRows = hover ? visible.map(s => ({ s, v: valueAt(s.points, hover.t, s.kind === "step") })).filter(r => r.v != null) : [];
  const nearMoments = hover ? data.moments.filter(m => Math.abs(m.ts - hover.t) <= Math.max(300, (x1 - x0) / 120)) : [];
  const groups = ["temp", "body", "skin", "presence", "env", "context", "raw"].map(l => ({ lane: l, items: all.filter(s => s.lane === l && !s.ghost) })).filter(g => g.items.length);
  const stats = visible.filter(s => s.lane !== "presence" && s.lane !== "raw" && (s.lane !== "context" || s.role === "location") && !s.ghost).map(s => {
    const ps = s.points.filter(p => p[0] >= x0 && p[0] <= x1);
    if (!ps.length) return null;
    const vs = ps.map(p => conv(s, p[1]));
    // time-weighted: each reading counts for the time until the next one (capped at 10 min across gaps), so the
    // server thinning flat stretches doesn't tilt the average
    let wsum = 0, tot = 0;
    for (let i = 0; i < ps.length; i++) {
      const wt = i + 1 < ps.length ? Math.min(600, Math.max(1, ps[i + 1][0] - ps[i][0])) : 60;
      wsum += vs[i] * wt; tot += wt;
    }
    let mn = Infinity, mx = -Infinity;
    for (const v of vs) { if (v < mn) mn = v; if (v > mx) mx = v; }
    return { s, avg: wsum / tot, min: mn, max: mx };
  }).filter(Boolean) as { s: Series; avg: number; min: number; max: number }[];
  const fmtV = (s: Series, v: number) => (s.role === "home" ? (v >= 0.5 ? "phone at home" : "phone away")
    : s.role === "location" ? (v < 0.3 ? "home" : `${v.toFixed(1)} km from home`)
    : s.role === "calendar" ? (v > 0 ? `busy until ${new Date(v * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone: data.tz })}` : "free")
    : s.role === "light" ? (v >= 0.5 ? "on" : "off") : s.unit === "°C" ? `${conv(s, v).toFixed(1)}°${units}` : s.lane === "presence" ? (v >= (s.role === "weight" ? 20 : 0.5) ? (s.role === "weight" ? `${weight(v, wu, 1)} · occupied` : "occupied") : (s.role === "weight" ? `${weight(v, wu, 1)} · empty` : "empty")) : s.id === "status" ? `${v}` : `${Math.round(v * 10) / 10} ${s.unit}`);

  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
        <div><div className="k">Analyze</div><h1 style={{ fontSize: 32 }}>Nights explorer</h1>
          <p className="mute" style={{ fontSize: 13 }}>Hover anywhere for values and their source · drag to zoom · double-click to reset</p></div>
        <div className="pills">
          {snap.people.map(p => <button key={p.id} className="pill" aria-pressed={p.id === person.id} onClick={() => { setPid(p.id); setDate(null); setZoom(null); }}>{p.name}</button>)}
          <select value={date ?? ""} onChange={e => { setDate(e.target.value); setZoom(null); }}>{nights.map(n => <option key={n.date} value={n.date}>{n.in_progress ? "Tonight" : n.label}{n.has_data ? "" : " (no data)"}</option>)}</select>
          <select value={cmpDate} onChange={e => setCmpDate(e.target.value)} aria-label="Compare with"><option value="">Compare with…</option>{nights.filter(n => n.date !== date && n.has_data).map(n => <option key={n.date} value={n.date}>{n.label}</option>)}</select>
        </div>
      </div>

      <div className="grid2 explore-grid">
        <div className="box" style={{ padding: "12px 12px 8px", position: "relative" }}>
          <div className="row" style={{ fontSize: 12.5 }}>
            <span className="mute">{fmtT(x0)} – {fmtT(x1)}{zoom ? " · zoomed" : ""}</span>
            <span className="pills">
              {data.schedule && <button className="pill" onClick={() => setZoom([data.schedule!.bedtime - 3600, data.schedule!.bedtime + 5400])}>Bedtime</button>}
              {data.phases.find(p => p.id === "deep") && <button className="pill" onClick={() => { const d = data.phases.find(p => p.id === "deep")!; const nx = data.phases.find(p => p.start > d.start); setZoom([d.start - 900, (nx?.start ?? d.start + 4 * 3600) + 900]); }}>Deep</button>}
              {data.schedule && <button className="pill" onClick={() => setZoom([data.schedule!.wake - 5400, data.schedule!.wake + 1800])}>Wake</button>}
              <button className="pill" onClick={() => setZoom(null)} disabled={!zoom}>Whole night</button>
            </span>
          </div>
          <div ref={wrap} style={{ position: "relative" }}>
            <svg viewBox={`0 0 ${w} ${H}`} width="100%" style={{ display: "block", cursor: "crosshair", userSelect: "none" }}
              onPointerMove={onMove} onPointerLeave={() => { setHover(null); }}
              onPointerDown={e => { const r = e.currentTarget.getBoundingClientRect(); const px = ((e.clientX - r.left) / r.width) * w; if (px >= L) { e.preventDefault(); setDrag({ x0: px, x1: px }); try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* ignore */ } } }}
              onPointerUp={onUp} onDoubleClick={() => setZoom(null)}>
              {/* phases as faint bands */}
              {data.phases.map((p, i) => { const nx = data.phases[i + 1]; const a = Math.max(p.start, x0), b = Math.min(nx?.start ?? data.end, x1); if (b <= a) return null;
                return <g key={p.id}><rect x={X(a)} y={EV - 4} width={X(b) - X(a)} height={H - AX - EV + 4} fill={i % 2 ? "var(--text)" : "transparent"} opacity=".025" />
                  {X(b) - X(a) > p.name.length * 6.2 + 8 && <text x={X(a) + 4} y={EV + 10} fontSize="10.5" fill="var(--faint)">{p.name}</text>}</g>; })}
              {/* events row */}
              <text x={L - 8} y={20} fontSize="11" textAnchor="end" fill="var(--mute)">Events</text>
              {momentsIn.map((m, i) => <text key={i} x={X(m.ts)} y={22} fontSize="14" textAnchor="middle" fill={m.kind === "hot_flash" ? "var(--cool)" : m.kind === "cold_flash" ? "var(--warm)" : "var(--text)"} style={{ cursor: "pointer" }}
                onClick={() => setZoom([m.ts - 1800, m.ts + 2400])}>{MOMENT_GLYPH[m.kind] ?? "·"}</text>)}
              {laneBox.map(b => (
                <g key={b.id}>
                  <text x={8} y={b.y0 - 3} fontSize="10.5" fill="var(--faint)" style={{ textTransform: "uppercase", letterSpacing: ".08em" }}>{b.label}</text>
                  {b.id !== "presence" && b.id !== "stages" && b.id !== "context" && (() => { const s = scales[b.id]; const u = visible.find(x => x.lane === b.id); return <>
                    {[0, 0.5, 1].map(f => { const v = s.lo + (s.hi - s.lo) * f; const yy = b.y1 - f * (b.y1 - b.y0);
                      return <g key={f}><line x1={L} x2={w - R} y1={yy} y2={yy} stroke="var(--text)" strokeOpacity=".06" />
                        <text x={L - 8} y={yy + 4} fontSize="10.5" textAnchor="end" fill="var(--faint)">{Math.round(v)}{u ? (u.unit === "°C" ? "°" : "") : ""}</text></g>; })}
                    {visible.filter(x => x.lane === b.id).map(s => <path key={s.id} d={pathFor(s)} fill="none" strokeLinecap="round" strokeLinejoin="round" stroke={s.color} strokeWidth={s.id === "target" ? 2.4 : 1.4}
                      strokeOpacity={s.ghost ? 0.35 : s.id === "measured" ? 0.55 : 0.95} strokeDasharray={s.ghost ? "4 4" : undefined} />)}
                  </>; })()}
                  {b.id === "presence" && presenceRows(b)}
                  {b.id === "context" && contextRows(b)}
                  {b.id === "stages" && stageSrcs.map((src, i) => { const y0 = b.y0 + 4 + i * 46; const rowH = 10;
                    return <g key={src.id}>
                      <text x={L - 8} y={y0 + 14} fontSize="11" textAnchor="end" fill="var(--mute)">{src.kind === "estimate" ? "Bed estimate" : src.label.split(" · ")[0]}</text>
                      <text x={L - 8} y={y0 + 27} fontSize="9.5" textAnchor="end" fill="var(--faint)">{src.kind}{src.agree_with_first != null ? ` · ${Math.round(src.agree_with_first * 100)}% match` : ""}</text>
                      {STAGE_LV.map((lv, k) => <line key={lv} x1={L} x2={w - R} y1={y0 + k * rowH + rowH / 2} y2={y0 + k * rowH + rowH / 2} stroke="var(--line)" />)}
                      {src.segments.filter(g => g.end >= x0 && g.start <= x1).map((g, k) => <rect key={k} x={X(Math.max(g.start, x0))} y={y0 + STAGE_LV.indexOf(g.stage) * rowH + 1} width={Math.max(1, X(Math.min(g.end, x1)) - X(Math.max(g.start, x0)))} height={rowH - 2} rx="2" fill={STAGE_COLOR[g.stage]} />)}
                    </g>; })}
                </g>
              ))}
              {ticks.map(t => <text key={t} x={X(t)} y={H - 8} fontSize="11" textAnchor="middle" fill="var(--faint)">{fmtT(t)}</text>)}
              {data.now >= x0 && data.now <= x1 && <line x1={X(data.now)} x2={X(data.now)} y1={EV - 6} y2={H - AX} stroke="var(--acc)" strokeOpacity=".5" strokeDasharray="2 3" />}
              {hover && <line x1={X(hover.t)} x2={X(hover.t)} y1={6} y2={H - AX} stroke="var(--text)" strokeOpacity=".35" pointerEvents="none" />}
              {hover && laneBox.filter(b => b.id !== "presence" && b.id !== "stages" && b.id !== "context").flatMap(b => tipRows.filter(r => r.s.lane === b.id).map(r =>
                <circle key={r.s.id} cx={X(hover.t)} cy={Yf(b.id, conv(r.s, r.v!))} r="3.5" fill={r.s.color} />))}
              {drag && <rect x={Math.min(drag.x0, drag.x1)} y={EV - 6} width={Math.abs(drag.x1 - drag.x0)} height={H - AX - EV + 6} fill="var(--acc)" opacity=".1" />}
            </svg>
            {hover && !drag && (
              <div style={{ position: "absolute", left: hover.x + 18 + 314 <= (wrap.current?.clientWidth ?? 800) ? hover.x + 18 : Math.max(0, hover.x - 18 - 314), top: Math.max(0, Math.min(hover.y - 40, H * ((wrap.current?.clientWidth ?? w) / w) - 120)), width: 314, pointerEvents: "none",
                background: "var(--raise)", border: "1px solid var(--line2)", borderRadius: 12, padding: "10px 12px", fontSize: 12.5, boxShadow: "0 12px 30px rgba(0,0,0,.4)", zIndex: 5 }}>
                <div className="row" style={{ marginBottom: 6 }}><b style={{ fontWeight: 600 }}>{clock(hover.t, data.tz)}</b><span className="mute">{phaseAt(hover.t)?.name ?? "before rhythm"}</span></div>
                {tipRows.map(({ s, v }) => (
                  <div key={s.id} style={{ display: "grid", gridTemplateColumns: "10px 1fr auto", gap: "0 8px", padding: "3px 0", borderTop: "1px solid var(--line)" }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: s.color, marginTop: 5, opacity: (s as Series & { ghost?: boolean }).ghost ? 0.5 : 1 }} />
                    <span>{s.label}<br /><span className="faint" style={{ fontSize: 11 }}>{s.source}</span></span>
                    <b style={{ fontWeight: 500 }} className="num">{fmtV(s, v!)}</b>
                  </div>
                ))}
                {stageSrcs.map(src => { const g = src.segments.find(x => x.start <= hover.t && x.end > hover.t); if (!g) return null;
                  return <div key={src.id} style={{ display: "grid", gridTemplateColumns: "10px 1fr auto", gap: "0 8px", padding: "3px 0", borderTop: "1px solid var(--line)" }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: STAGE_COLOR[g.stage], marginTop: 5 }} />
                    <span>Sleep stage<br /><span className="faint" style={{ fontSize: 11 }}>{src.label}</span></span>
                    <b style={{ fontWeight: 500 }}>{g.stage === "rem" ? "REM" : g.stage}</b></div>; })}
                {nearMoments.map((m, i) => <div key={i} style={{ borderTop: "1px solid var(--line)", paddingTop: 4, marginTop: 2 }}>{MOMENT_GLYPH[m.kind]} {momentLabel(m)} · {clock(m.ts, data.tz)}{m.minutes ? ` · ${Math.round(m.minutes)} min` : ""}{m.count && m.count > 1 ? ` · ${m.count}×` : ""}{sourceText(m.source) ? ` · ${sourceText(m.source)}` : ""}</div>)}
                {!tipRows.length && !nearMoments.length && !stageSrcs.some(src => src.segments.some(x => x.start <= hover.t && x.end > hover.t)) && <span className="mute">No data at this moment.</span>}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div className="box"><span className="k">Data sources</span>
            {groups.map(g => (
              <div key={g.lane} style={{ display: "grid", gap: 4 }}>
                <span className="faint" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".08em", marginTop: 4 }}>{LANES.find(l => l.id === g.lane)?.label}</span>
                {g.items.map(s => (
                  <label key={s.id} className="srcrow" style={{ opacity: s.points.length ? 1 : 0.45 }} title={`${s.source}\n${s.device} · ${driverName(s.driver)}`}>
                    <input type="checkbox" checked={!off.has(s.id)} onChange={e => { const n = new Set(off); if (e.target.checked) n.delete(s.id); else n.add(s.id); setOff(n); if (s.lane === "raw" && e.target.checked) setShowRaw(true); }} />
                    <span style={{ width: 10, height: 10, borderRadius: 2, background: s.color }} />
                    <span>{s.label}<br /><span className="faint" style={{ fontSize: 11 }}>{deviceLine(s.device, s.driver)}</span></span>
                    <span className="faint num" style={{ fontSize: 11 }} title="readings in this view">{s.points.length ? s.points.length.toLocaleString() : "no data"}</span>
                  </label>
                ))}
              </div>
            ))}
            {(data.stages ?? []).length > 0 && <div style={{ display: "grid", gap: 4 }}>
              <span className="faint" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".08em", marginTop: 4 }}>Sleep stages</span>
              {data.stages.map(src => (
                <label key={src.id} className="srcrow" title={src.label}>
                  <input type="checkbox" checked={!off.has(`stages:${src.id}`)} onChange={e => { const n = new Set(off); if (e.target.checked) n.delete(`stages:${src.id}`); else n.add(`stages:${src.id}`); setOff(n); }} />
                  <span style={{ width: 10, height: 10, borderRadius: 2, background: "linear-gradient(90deg,#3f6fb5,#7fb6cc,#a08be2,#e3b37a)" }} />
                  <span>{src.kind === "estimate" ? "Bed estimate" : src.label.split(" · ")[0]}<br /><span className="faint" style={{ fontSize: 11 }}>{src.label.split(" · ").slice(1).join(" · ") || src.kind}</span></span>
                  <span className="faint num" style={{ fontSize: 11 }}>{src.agree_with_first != null ? `${Math.round(src.agree_with_first * 100)}%` : "ref"}</span>
                </label>))}
            </div>}
            <label className="row" style={{ fontSize: 13 }}><span>Show unfiltered sensor readings</span><input type="checkbox" className="toggle" checked={showRaw} onChange={e => setShowRaw(e.target.checked)} /></label>
          </div>
          <div className="box"><span className="k">{zoom ? "Selected range" : "Whole night"}</span>
            <table className="t"><thead><tr><th>Series</th><th>avg</th><th>min</th><th>max</th></tr></thead>
              <tbody>{stats.map(({ s, avg, min, max }) => <tr key={s.id}><td><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: s.color, marginRight: 6 }} />{s.label}</td>
                <td className="num">{avg.toFixed(1)}</td><td className="num">{min.toFixed(1)}</td><td className="num">{max.toFixed(1)}</td></tr>)}</tbody></table>
            {stageSrcs.length > 0 && <table className="t"><thead><tr><th>Stages</th><th>deep</th><th>light</th><th>REM</th><th>awake</th></tr></thead>
              <tbody>{stageSrcs.map(src => { const tt: Record<string, number> = { deep: 0, light: 0, rem: 0, awake: 0 };
                src.segments.forEach(g => { const a2 = Math.max(g.start, x0), b2 = Math.min(g.end, x1); if (b2 > a2) tt[g.stage] += (b2 - a2) / 60; });
                return <tr key={src.id}><td>{src.kind === "estimate" ? "Bed estimate" : src.label.split(" · ")[0]}</td>{["deep", "light", "rem", "awake"].map(k => <td key={k} className="num">{Math.round(tt[k])}m</td>)}</tr>; })}</tbody></table>}
            <span className="faint" style={{ fontSize: 11 }}>Temperatures in °{units}{cmp ? `. Dashed lines are ${cmp.date}, aligned by clock time.` : ""}</span>
          </div>
        </div>
      </div>

      {ring && <RingCard ring={ring} units={units} date={data.date} />}
      {naps.list.length > 0 && <NapsCard naps={naps.list} nightMin={naps.night} />}
      <Sleep14 person={person.id} />
      <NightFix key={`${person.id}:${data.date}`} person={person.id} date={data.date} tz={data.tz} pets={pets}
        ups={data.moments.filter(m => m.kind === "out_of_bed").map(m => ({ ts: m.ts, minutes: m.minutes }))}
        onChanged={() => setBump(b => b + 1)} />
      <PetQuestions person={person.id} tz={data.tz} />
      <div className="box"><span className="k">Moments · click to zoom</span>
        <div className="moments" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <ShowMore items={data.moments} limit={12} noun="moments" render={shown => shown.map((m, i) => <button key={i} className="pill" onClick={() => setZoom([m.ts - 1800, m.ts + 2400])}>
            {MOMENT_GLYPH[m.kind]} {clock(m.ts, data.tz)} · {momentLabel(m)}{m.minutes ? `${m.kind === "pet" ? " · for" : ""} ${Math.round(m.minutes)} min` : ""}{m.count && m.count > 1 ? ` ×${m.count}` : ""}</button>)} />
          {!data.moments.length && <span className="mute">Nothing happened this night: no flashes, changes or trips out of bed.</span>}
        </div>
      </div>
    </>
  );
}
