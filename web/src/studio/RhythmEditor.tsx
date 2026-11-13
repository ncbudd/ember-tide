import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { useOwnPerson } from "../auth";
import { cToF, fToC, nightsText, personaColor } from "../format";
import type { Phase, Rhythm } from "../types";
import Tip from "../components/Tip";

// minutes on a nominal night: 12:00 → 0 … 11:59 next morning → 1439; evening times keep their clock minutes
const hmToM = (hm: string) => { const [h, m] = hm.split(":").map(Number); return (h < 12 ? h + 24 : h) * 60 + m; };
const mToHm = (m: number) => { const t = ((Math.round(m) % 1440) + 1440) % 1440; return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };
const fmtM = (m: number) => { const t = ((Math.round(m) % 1440) + 1440) % 1440, h = Math.floor(t / 60), mm = t % 60; return `${h % 12 || 12}:${String(mm).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };

function startMin(p: Phase, bed: number, wake: number): number {
  const a = p.start;
  if (a.kind === "clock") return hmToM(a.time ?? "03:00");
  return (a.kind === "wake" ? wake : bed) + a.offset_min;
}
function planAt(pts: { m: number; c: number; ramp: number }[], m: number, end: number): number | null {
  if (!pts.length || m < pts[0].m || m > end) return null;
  let i = 0;
  for (let k = 0; k < pts.length; k++) if (pts[k].m <= m) i = k;
  const prev = i ? pts[i - 1].c : pts[i].c;
  const f = pts[i].ramp <= 0 ? 1 : Math.min(1, (m - pts[i].m) / pts[i].ramp);
  return prev + (pts[i].c - prev) * f;
}

export default function RhythmEditor() {
  const { snap, say } = useApp();
  const mine = useOwnPerson();
  const [pid, setPid] = useState<string | null>(null);
  const [rhythms, setRhythms] = useState<Rhythm[] | null>(null);
  const [draft, setDraft] = useState<Rhythm | null>(null);
  type Drag = { i: number; x0: number; y0: number; c0: number; off0: number; time0: string | null };
  const [drag, setDragState] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);           // synchronous copy: the first move can arrive before React re-renders
  const setDrag = (d: Drag | null) => { dragRef.current = d; setDragState(d); };
  const [hover, setHover] = useState<number | null>(null);
  const [last, setLast] = useState<{ date: string; pts: [number, number][] } | null>(null);
  const [showLast, setShowLast] = useState(true);
  const [w, setW] = useState(900);
  const wrap = useRef<HTMLDivElement>(null);
  const person = snap?.people.find(p => p.id === (pid ?? mine ?? snap?.people[0]?.id));

  useEffect(() => { if (!wrap.current) return; const ro = new ResizeObserver(() => setW(Math.max(560, wrap.current!.clientWidth))); ro.observe(wrap.current); return () => ro.disconnect(); }, [draft?.id]);
  useEffect(() => {
    if (!person) return;
    api.get<Rhythm[]>(`/api/people/${person.id}/rhythms`).then(r => { setRhythms(r); const id = person.schedule?.rhythm_id ?? r[0].id; setDraft(structuredClone(r.find(x => x.id === id) ?? r[0])); });
    api.get<{ date: string; has_data: boolean; in_progress: boolean }[]>(`/api/people/${person.id}/nights?count=7`).then(async ns => {
      const n = ns.find(x => x.has_data && !x.in_progress);
      if (!n) { setLast(null); return; }
      const ex = await api.get<{ tz: string; series: { id: string; points: [number, number][] }[] }>(`/api/explore?person_id=${person.id}&date=${n.date}`);
      const meas = ex.series.find(s => s.id === "measured")?.points ?? [];
      const toM = (t: number) => { const d = new Date(t * 1000); const parts = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "numeric", hour12: false, timeZone: ex.tz }).formatToParts(d);
        const h = Number(parts.find(p => p.type === "hour")?.value ?? 0) % 24, m = Number(parts.find(p => p.type === "minute")?.value ?? 0); return (h < 12 ? h + 24 : h) * 60 + m; };
      setLast({ date: n.date, pts: meas.map(([t, c]) => [toM(t), c] as [number, number]) });
    }).catch(() => setLast(null));
  }, [person?.id]);
  if (!snap || !person || !rhythms || !draft) return null;

  const u = person.settings.units;
  const sch = draft.schedule;
  const inBedOwn = person?.settings.in_bed_after_min ?? null;      // the personal "lying down before the night starts" wins over confirm_min
  const bed = hmToM(sch.bedtime), wake = hmToM(sch.wake), end = wake + sch.after_wake_min;
  const pts = draft.phases.map(p => ({ m: startMin(p, bed, wake), c: p.temp_c, ramp: p.ramp_min }));
  // a phase that starts after the next one would be skipped by the engine: say so and don't let it be saved
  const outOfOrder = pts.slice(0, -1).map((p, i) => (p.m > pts[i + 1].m ? i : -1)).filter(i => i >= 0);
  for (let i = pts.length - 2; i >= 0; i--) pts[i].m = Math.min(pts[i].m, pts[i + 1].m);
  const x0 = Math.min(pts[0].m, hmToM(sch.window_start)) - 30, x1 = end + 30;
  const temps = [...pts.map(p => p.c), ...(showLast && last ? last.pts.map(p => p[1]) : [])];
  const lo = Math.min(fToC(58), ...temps) - 0.6, hi = Math.max(fToC(82), ...temps) + 0.6;
  const H = 340, L = 52, R = 18, T = 34, B = 54;
  const X = (m: number) => L + ((m - x0) / (x1 - x0)) * (w - L - R);
  const Y = (c: number) => T + (1 - (c - lo) / (hi - lo)) * (H - T - B);
  const toUnits = (c: number) => (u === "F" ? cToF(c) : c);
  const deg = (c: number, d = 0) => `${toUnits(c).toFixed(d)}°`;
  const color = personaColor(person);

  // curve: hold then linear ramp into each phase
  const curve: [number, number][] = [];
  pts.forEach((p, i) => { const prev = i ? pts[i - 1].c : p.c; curve.push([p.m, prev], [p.m + p.ramp, p.c]); });
  curve.push([end, pts[pts.length - 1].c]);
  const pathD = curve.map(([m, c], i) => `${i ? "L" : "M"}${X(m).toFixed(1)},${Y(c).toFixed(1)}`).join(" ");
  const areaD = `${pathD} L${X(end).toFixed(1)},${H - B} L${X(pts[0].m).toFixed(1)},${H - B} Z`;
  const ticks: number[] = []; for (let m = Math.ceil(x0 / 60) * 60; m <= x1; m += 60) ticks.push(m);
  const gridF = u === "F" ? [56, 60, 64, 68, 72, 76, 80, 84, 88].filter(f => fToC(f) > lo && fToC(f) < hi).map(fToC) : [14, 16, 18, 20, 22, 24, 26, 28, 30].filter(c => c > lo && c < hi);
  const lastPath = showLast && last ? last.pts.filter(p => p[0] >= x0 && p[0] <= x1).map(([m, c], i) => `${i ? "L" : "M"}${X(m).toFixed(1)},${Y(c).toFixed(1)}`).join(" ") : "";

  const setPhase = (i: number, patch: Partial<Phase>) => { const d = structuredClone(draft); d.phases[i] = { ...d.phases[i], ...patch }; setDraft(d); };
  const pxScale = () => (wrap.current ? w / wrap.current.getBoundingClientRect().width : 1);
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * w;
    setHover(Math.min(x1, Math.max(x0, x0 + ((px - L) / (w - L - R)) * (x1 - x0))));
    const drag = dragRef.current;
    if (!drag) return;
    const s = pxScale();
    const dy = (e.clientY - drag.y0) * s, dx = (e.clientX - drag.x0) * s;
    let c = drag.c0 - (dy / (H - T - B)) * (hi - lo);
    c = u === "F" ? fToC(Math.round(cToF(c))) : Math.round(c * 2) / 2;
    c = Math.min(fToC(95), Math.max(fToC(55), c));
    const dm = Math.round(((dx / (w - L - R)) * (x1 - x0)) / 5) * 5;
    const p = draft.phases[drag.i];
    const start = p.start.kind === "clock" ? { ...p.start, time: mToHm(hmToM(drag.time0 ?? "03:00") + dm) } : { ...p.start, offset_min: Math.max(-720, Math.min(720, drag.off0 + dm)) };
    setPhase(drag.i, { temp_c: Math.round(c * 100) / 100, start });
  };
  const saved = rhythms.find(x => x.id === draft.id)!;
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const save = async () => {
    try { await api.put(`/api/people/${person.id}/rhythms/${draft.id}`, draft); setRhythms(rhythms.map(r => (r.id === draft.id ? structuredClone(draft) : r))); say("Rhythm saved"); }
    catch (e) { say(`Couldn't save: ${(e as Error).message}`); }
  };
  const hv = hover != null ? { plan: planAt(pts, hover, end), last: showLast && last ? (last.pts.reduce<[number, number] | null>((b, p) => (!b || Math.abs(p[0] - hover) < Math.abs(b[0] - hover) ? p : b), null)) : null, ph: [...pts].map((p, i) => ({ ...p, i })).reverse().find(p => p.m <= hover) } : null;
  const anchorWord = (p: Phase) => p.start.kind === "clock" ? `at ${fmtM(hmToM(p.start.time ?? "03:00"))}` : `${p.start.offset_min === 0 ? "at" : `${Math.abs(p.start.offset_min)} min ${p.start.offset_min < 0 ? "before" : "after"}`} ${p.start.kind === "bedtime" ? "bedtime" : p.start.kind === "in_bed" ? "getting in bed" : "wake"}`;

  return (
    <>
      <div className="row" style={{ flexWrap: "wrap", alignItems: "flex-end" }}>
        <div><div className="k">Configure</div><h1 style={{ fontSize: 32 }}>Rhythm editor</h1>
          <p className="mute" style={{ fontSize: 13 }}>Drag a point up or down to change its temperature, sideways to change when the phase starts. Hover to read the plan.</p></div>
        <div className="pills" style={{ alignItems: "center" }}>
          {snap.people.map(p => <button key={p.id} className="pill" aria-pressed={p.id === person.id} disabled={dirty && p.id !== person.id} title={dirty && p.id !== person.id ? "Save or revert your changes first" : undefined} onClick={() => { setPid(p.id); setRhythms(null); setDraft(null); }}>{p.name}</button>)}
          <span style={{ width: 1, height: 22, background: "var(--line2)" }} />
          {rhythms.map(r => <button key={r.id} className="pill" aria-pressed={r.id === draft.id} disabled={dirty && r.id !== draft.id} title={dirty && r.id !== draft.id ? "Save or revert your changes first" : undefined} onClick={() => setDraft(structuredClone(r))}>{r.name}</button>)}
        </div>
      </div>

      <div className="box" style={{ padding: "14px 16px 10px" }}>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <span><b style={{ fontWeight: 500 }}>{person.name} · {draft.name}</b> <span className="mute" style={{ fontSize: 13 }}>· {draft.nights === "work" ? "before a work day: " : draft.nights === "off" ? "before a day off: " : ""}{draft.days.length ? nightsText(draft.days).replace("Every night", "every night") : "no nights picked yet"}</span></span>
          <span className="pills" style={{ alignItems: "center" }}>
            {last && <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center", color: "var(--mute)" }}><input type="checkbox" checked={showLast} onChange={e => setShowLast(e.target.checked)} />Last night's bed ({new Date(`${last.date}T12:00`).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })})</label>}
            {dirty && <span className="chip" style={{ cursor: "default", color: "var(--warn)", borderColor: "var(--warn)" }}>Unsaved changes</span>}
            <button className="btn" disabled={!dirty} onClick={() => setDraft(structuredClone(saved))}>Revert</button>
            <button className="btn pri" disabled={!dirty || outOfOrder.length > 0} onClick={save}>Save rhythm</button>
          </span>
        </div>
        {outOfOrder.length > 0 && <p role="alert" style={{ fontSize: 13, color: "var(--warn)", margin: "4px 0 0" }}>
          {outOfOrder.map(i => `${draft.phases[i].name} now starts after ${draft.phases[i + 1].name}`).join("; ")}. Phases have to stay in order, so move one of them before saving.</p>}
        <div ref={wrap} style={{ position: "relative" }}>
          <svg viewBox={`0 0 ${w} ${H}`} width="100%" style={{ display: "block", touchAction: "none", userSelect: "none", cursor: drag ? "grabbing" : "crosshair" }}
            onPointerMove={onMove} onPointerUp={e => { if (dragRef.current) onMove(e); setDrag(null); }} onPointerLeave={() => { setDrag(null); setHover(null); }}>
            <defs>
              <linearGradient id="re-area" x1="0" y1={Y(hi)} x2="0" y2={Y(lo)} gradientUnits="userSpaceOnUse">
                <stop offset="0" stopColor="var(--warm)" stopOpacity=".28" /><stop offset=".6" stopColor="var(--cool)" stopOpacity=".18" /><stop offset="1" stopColor="var(--cool)" stopOpacity=".02" />
              </linearGradient>
            </defs>
            {/* phase bands */}
            {pts.map((p, i) => { const b = i + 1 < pts.length ? pts[i + 1].m : end; if (b <= p.m) return null;
              return <g key={i}><rect x={X(p.m)} y={T - 22} width={X(b) - X(p.m)} height={H - B - T + 22} fill={i % 2 ? "var(--text)" : "transparent"} opacity=".028" />
                <line x1={X(p.m)} x2={X(p.m)} y1={T - 22} y2={H - B} stroke="var(--line)" />
                {/* the name only where it fits inside its band (narrow phases would overlap the next one) */}
                {X(b) - X(p.m) >= draft.phases[i].name.length * 6.4 + 12 && <text x={X(p.m) + 6} y={T - 8} fontSize="11.5" fill="var(--mute)">{draft.phases[i].name}</text>}
                <title>{draft.phases[i].name}</title></g>; })}
            {/* grid */}
            {gridF.map(c => <g key={c}><line x1={L} x2={w - R} y1={Y(c)} y2={Y(c)} stroke="var(--text)" strokeOpacity=".055" />
              <text x={L - 10} y={Y(c) + 4} fontSize="11" textAnchor="end" fill="var(--faint)">{Math.round(toUnits(c))}°</text></g>)}
            {/* when the night may start */}
            <rect x={X(hmToM(sch.window_start))} y={H - B + 8} width={Math.max(2, X(Math.min(hmToM(sch.window_end), x1)) - X(hmToM(sch.window_start)))} height="6" rx="3" fill="var(--acc)" opacity=".25" />
            <text x={X(hmToM(sch.window_start))} y={H - B + 28} fontSize="10.5" fill="var(--faint)">night can start {fmtM(hmToM(sch.window_start))} · keep pre-conditioning an empty side until {fmtM(hmToM(sch.window_end))}</text>
            {/* bedtime + wake */}
            {[[bed, `In bed ~${fmtM(bed)}`], [wake, `Wake ${fmtM(wake)}`]].map(([m, l]) => <g key={l as string}>
              <line x1={X(m as number)} x2={X(m as number)} y1={T - 22} y2={H - B} stroke="var(--text)" strokeOpacity=".28" strokeDasharray="3 4" />
              <rect x={X(m as number) - 52} y={H - B - 22} width="104" height="18" rx="9" fill="var(--raise)" stroke="var(--line2)" />
              <text x={X(m as number)} y={H - B - 9} fontSize="10.5" textAnchor="middle" fill="var(--mute)">{l}</text></g>)}
            {/* last night + plan */}
            {lastPath && <path d={lastPath} fill="none" stroke="var(--text)" strokeOpacity=".35" strokeWidth="1.2" />}
            <path d={areaD} fill="url(#re-area)" />
            <path d={pathD} fill="none" stroke={color} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
            {ticks.map(m => <text key={m} x={X(m)} y={H - 6} fontSize="11" textAnchor="middle" fill="var(--faint)">{fmtM(m).replace(":00", "")}</text>)}
            {/* handles */}
            {pts.map((p, i) => { const cx = X(p.m + p.ramp), cy = Y(p.c), active = drag?.i === i;
              return <g key={i} style={{ cursor: "grab" }} onPointerDown={e => { e.preventDefault(); const ph = draft.phases[i]; setDrag({ i, x0: e.clientX, y0: e.clientY, c0: ph.temp_c, off0: ph.start.offset_min, time0: ph.start.time });
                try { (e.currentTarget.ownerSVGElement as SVGSVGElement).setPointerCapture(e.pointerId); } catch { /* synthetic or already released pointer */ } }}>
                <circle cx={cx} cy={cy} r="18" fill="transparent" />
                <rect x={cx - 22} y={cy - 32} width="44" height="20" rx="10" fill={active ? "var(--acc)" : "var(--raise)"} stroke="var(--line2)" />
                <text x={cx} y={cy - 18} fontSize="12" textAnchor="middle" fill={active ? "var(--bg)" : "var(--text)"} fontWeight="500">{deg(p.c)}</text>
                <circle cx={cx} cy={cy} r={active ? 9 : 7} fill="var(--bg)" stroke={color} strokeWidth="3" />
              </g>; })}
            {hover != null && !drag && <line x1={X(hover)} x2={X(hover)} y1={T - 22} y2={H - B} stroke="var(--text)" strokeOpacity=".4" pointerEvents="none" />}
          </svg>
          {hover != null && hv && (
            <div className="scrubtip" style={{ left: (() => { const cw = wrap.current?.clientWidth ?? w; const cx = (X(hover) / w) * cw; return cx + 16 + 186 <= cw ? cx + 16 : Math.max(0, cx - 16 - 186); })(), top: 40, width: 186 }}>
              <div className="row"><b>{fmtM(hover)}</b><span className="mute">{hv.ph ? draft.phases[hv.ph.i].name : "before wind-down"}</span></div>
              <span>plan <b>{hv.plan != null ? deg(hv.plan, 1) : "off"}</b></span>
              {hv.last && Math.abs(hv.last[0] - hover) < 10 && <span className="mute">last night's bed {deg(hv.last[1], 1)}</span>}
            </div>
          )}
        </div>
      </div>

      <div className="grid2 wide re-grid">
        <div className="box">
          <span className="k">Phases</span>
          <span className="faint" style={{ fontSize: 12 }}>Each part of the night: its temperature, when it starts (relative to bedtime, getting into bed, a clock time or wake), and how many minutes it takes to change to it. Drag the dots on the chart or use the table. "Autopilot" marks the parts it may adjust.</span>
          <div className="tscroll">
          <table className="t re-phases">
            <thead><tr><th>Phase</th><th>Temp</th><th>Starts</th><th>Offset</th><th>Change over</th><th>Autopilot</th></tr></thead>
            <tbody>{draft.phases.map((p, i) => (
              <tr key={p.id}>
                <td><b style={{ fontWeight: 500 }}>{p.name}</b><br /><span className="faint" style={{ fontSize: 11.5 }}>{anchorWord(p)}</span></td>
                <td><span className="stepper"><button aria-label="cooler" onClick={() => setPhase(i, { temp_c: Math.round((p.temp_c - (u === "F" ? 5 / 9 : 0.5)) * 100) / 100 })}>−</button>
                  <span className="num" style={{ minWidth: 36, textAlign: "center" }}>{deg(p.temp_c)}</span>
                  <button aria-label="warmer" onClick={() => setPhase(i, { temp_c: Math.round((p.temp_c + (u === "F" ? 5 / 9 : 0.5)) * 100) / 100 })}>+</button></span></td>
                <td><select value={p.start.kind} onChange={e => setPhase(i, { start: { ...p.start, kind: e.target.value as Phase["start"]["kind"], time: e.target.value === "clock" ? p.start.time ?? "03:00" : null } })}>
                  <option value="bedtime">bedtime</option><option value="in_bed">getting in bed</option><option value="clock">clock time</option><option value="wake">wake</option></select></td>
                <td>{p.start.kind === "clock" ? <input type="time" value={p.start.time ?? "03:00"} onChange={e => setPhase(i, { start: { ...p.start, time: e.target.value } })} />
                  : <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><input type="number" step={5} min={-720} max={720} style={{ width: 72 }} value={p.start.offset_min} onChange={e => setPhase(i, { start: { ...p.start, offset_min: Math.max(-720, Math.min(720, parseInt(e.target.value || "0") || 0)) } })} /><span className="faint">min</span></span>}</td>
                <td><select value={p.ramp_min} onChange={e => setPhase(i, { ramp_min: parseInt(e.target.value) })}>{[0, 5, 10, 15, 20, 30, 45, 60].map(m => <option key={m} value={m}>{m ? `${m} min` : "immediate"}</option>)}</select></td>
                <td><input type="checkbox" className="toggle" checked={p.autopilot} onChange={e => setPhase(i, { autopilot: e.target.checked })} /></td>
              </tr>))}</tbody>
          </table></div>
        </div>
        <div className="box">
          <span className="k">Schedule</span>
          {([["bedtime", "Usually in bed", "Used to time the pre-condition and as the start when presence is off"], ["wake", "Wake", "Warm wake is timed from this (or from your phone alarm)"],
             ["window_start", "Earliest start", "Getting in bed before this doesn't start the night"], ["window_end", "Keep pre-conditioning an empty side until", "After this an empty side stops cooling; getting in still starts the night"]] as const).map(([k, l, help]) => (
            <label key={k} className="row" style={{ fontSize: 13.5, padding: "6px 0", borderBottom: "1px solid var(--line)" }}><span>{l}<br /><span className="faint" style={{ fontSize: 11.5 }}>{help}</span></span>
              <input type="time" value={sch[k]} onChange={e => setDraft({ ...draft, schedule: { ...sch, [k]: e.target.value } })} /></label>))}
          {([["precool_lead_min", "Start pre-conditioning before bedtime", "Time for the pad to reach your first temperature. Recommended: 45 min.", 180],
             ["confirm_min", "In bed this long before starting", inBedOwn != null ? `Your own setting (${inBedOwn} min, in Your settings → Sleep data) is used instead for every rhythm.` : "Stops a quick sit on the bed from starting the night. Recommended: 3 min.", 30],
             ["absent_hold_min", "Hold my side when I get up", "Keeps your temperature while you're up, then turns off until you're back. Recommended: 30 min.", 240],
             ["after_wake_min", "Turn off after wake", "How long the warm wake carries on after your wake time. Recommended: 15 min.", 180]] as const).map(([k, l, help, max]) => (
            <label key={k} className="row" style={{ fontSize: 13.5, padding: "6px 0", borderBottom: "1px solid var(--line)" }}><span>{l}<br /><span className="faint" style={{ fontSize: 11.5 }}>{help}</span></span>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><input type="number" min={0} max={max} disabled={k === "confirm_min" && inBedOwn != null} style={{ width: 72 }} value={sch[k]} onChange={e => setDraft({ ...draft, schedule: { ...sch, [k]: Math.max(0, Math.min(max, parseInt(e.target.value || "0") || 0)) } })} /><span className="faint">min</span></span></label>))}
          <label className="row" style={{ fontSize: 13.5, padding: "6px 0" }}><span>Start when I'm in bed on my side</span><input type="checkbox" className="toggle" checked={sch.start_on_presence} onChange={e => setDraft({ ...draft, schedule: { ...sch, start_on_presence: e.target.checked } })} /></label>
          {sch.start_on_presence && <label className="row" style={{ fontSize: 13.5, padding: "6px 0" }}><span>Wait until I'm settling to sleep<Tip label="About waiting until you settle" text="In bed before your usual bedtime (to read, say)? Your side holds its getting-into-bed temperature, and the later phases start once you settle to sleep: the bed goes still and your heart rate drops, your phone goes on its bedside charger or into bedtime mode, or the lights go off. At your usual bedtime at the latest." /></span><input type="checkbox" className="toggle" checked={sch.wait_for_sleep ?? true} onChange={e => setDraft({ ...draft, schedule: { ...sch, wait_for_sleep: e.target.checked } })} /></label>}
        </div>
      </div>
    </>
  );
}
