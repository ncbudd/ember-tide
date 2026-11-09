import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api";
import Fold from "../components/Fold";
import NightFix from "../components/NightFix";
import RingCard, { type Ring } from "../components/RingCard";
import NapsCard, { type Nap } from "../components/NapsCard";
import PetQuestion from "../components/PetQuestion";
import Tip from "../components/Tip";
import { useApp } from "../ctx";
import { clock, cToF, deltaText, hm12, sourceText, tempNum } from "../format";

interface Moment { kind: string; ts: number; end?: number; who?: string; pet?: string | null; corrected?: boolean; count?: number; delta_c?: number; temp_c?: number | null; minutes?: number; extended?: number; stopped_at?: number; source?: string | null; phase?: string | null; phase_name?: string | null; home?: boolean | null; why?: string; text?: string; with_person?: boolean; fitted?: boolean }
interface Night {
  date: string; label: string; in_progress: boolean; has_data: boolean; feedback: { temp?: string; note?: string; rested?: number } | null;
  voice?: { tone: string; checkin: string; story: string | null };     // the check-in question in this person's tone (voice.py)
  in_bed_at?: number | null; out_at?: number | null; in_bed_min?: number; settled_min?: number; fell_asleep_est?: number | null; time_to_settle_min?: number | null;
  times_up?: number; up_minutes?: number; hr_avg?: number | null; hr_low?: number | null; br_avg?: number | null; tracking_c?: number | null;
  coolest_c?: number | null; warmest_c?: number | null; hot_flashes?: number; cold_flashes?: number; adjustments?: number; moments: Moment[];
  series?: { ts: number; target: number | null; measured: number | null; hr: number | null; occupied: number }[];
  ring?: Ring | null; naps?: Nap[]; nap_min?: number; night_asleep_min?: number | null;
  stages?: StageSrc | null; stage_sources?: { id: string; label: string; kind: string }[];
}
interface StageSrc { id: string; label: string; kind: string; segments: { start: number; end: number; stage: string }[]; totals: Record<string, number>; agree_with_first?: number | null }
const STAGE_COLOR: Record<string, string> = { awake: "#e3b37a", rem: "#a08be2", light: "#7fb6cc", deep: "#3f6fb5" };
const STAGE_ROWS = ["awake", "rem", "light", "deep"];

function Hypnogram({ src, tz }: { src: StageSrc; tz: string }) {
  const g = src.segments;
  const W = 340, rowH = 16, H = rowH * 4 + 22;
  const t0 = g[0]?.start ?? 0, t1 = g[g.length - 1]?.end ?? 1;
  const sc = useScrub(W, t0, t1);
  if (!g.length) return null;
  const cur = sc.st ? g.find(x => x.start <= sc.st!.t && x.end > sc.st!.t) : null;
  const X = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * W;
  const ticks: number[] = [];
  for (let t = Math.ceil(t0 / 7200) * 7200; t < t1; t += 7200) ticks.push(t);
  return (
    <div ref={sc.ref} style={{ position: "relative" }} {...sc.handlers}>
    {sc.st && <div className="scrubtip" style={{ left: sc.tipLeft(sc.st.px), top: sc.st.mouse ? 4 : -48 }}>
      <div className="row"><b>{clock(sc.st.t, tz)}</b><span style={{ color: cur ? STAGE_COLOR[cur.stage] : undefined }}>{cur ? (cur.stage === "rem" ? "REM" : cur.stage[0].toUpperCase() + cur.stage.slice(1)) : "no data"}</span></div>
      {cur && <span className="mute">{clock(cur.start, tz)} – {clock(cur.end, tz)}</span>}</div>}
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", touchAction: "pan-y" }} role="img" aria-label="Sleep stages through the night. Slide along it to see each stage.">
      {STAGE_ROWS.map((r, i) => <line key={r} x1="0" x2={W} y1={i * rowH + rowH / 2} y2={i * rowH + rowH / 2} stroke="var(--line)" />)}
      {g.map((x, i) => { const row = STAGE_ROWS.indexOf(x.stage); return <rect key={i} x={X(x.start)} y={row * rowH + 2} width={Math.max(1, X(x.end) - X(x.start))} height={rowH - 4} rx="2" fill={STAGE_COLOR[x.stage]} />; })}
      {ticks.map(t => <text key={t} x={X(t)} y={H - 4} fontSize="9" textAnchor="middle" fill="var(--faint)">{new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", timeZone: tz })}</text>)}
      {sc.st && <line x1={X(sc.st.t)} x2={X(sc.st.t)} y1={0} y2={rowH * 4} stroke="var(--text)" strokeOpacity=".5" />}
    </svg>
    </div>
  );
}
const hm = (m: number | undefined | null) => (m == null ? "—" : m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);

function useScrub(W: number, t0: number, t1: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [st, setSt] = useState<{ t: number; px: number; mouse?: boolean } | null>(null);
  const timer = useRef<number>();
  const at = (cx: number) => { const r = ref.current!.getBoundingClientRect(); const f = Math.min(1, Math.max(0, (cx - r.left) / r.width)); return { t: t0 + f * (t1 - t0), px: cx - r.left }; };
  const handlers = {
    onPointerDown: (e: React.PointerEvent) => { clearTimeout(timer.current); setSt({ ...at(e.clientX), mouse: e.pointerType === "mouse" }); },
    onPointerMove: (e: React.PointerEvent) => { clearTimeout(timer.current); setSt({ ...at(e.clientX), mouse: e.pointerType === "mouse" }); },
    onPointerUp: (e: React.PointerEvent) => { timer.current = window.setTimeout(() => setSt(null), e.pointerType === "mouse" ? 0 : 1800); },
    onPointerLeave: (e: React.PointerEvent) => { timer.current = window.setTimeout(() => setSt(null), e.pointerType === "mouse" ? 0 : 1800); },
  };
  const w = ref.current?.clientWidth ?? 340;
  const tipLeft = (px: number) => (st?.mouse ? (px + 14 + 180 <= w ? px + 14 : Math.max(0, px - 14 - 180)) : Math.max(4, Math.min(w - 184, px - 90)));
  return { ref, st, handlers, X: (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * W, w, tipLeft };
}

function NightChart({ n, units, tz }: { n: Night; units: "F" | "C"; tz: string }) {
  const s = n.series ?? [];
  const W = 340, H = 150, T = 8, B = 34;
  const x0 = s[0]?.ts ?? 0, x1 = s[s.length - 1]?.ts ?? 1;
  const sc = useScrub(W, x0, x1);
  if (s.length < 3) return null;
  const temps = s.flatMap(p => [p.target, p.measured]).filter((v): v is number => v != null);
  if (!temps.length) return null;                            // a night with no temperatures yet: nothing to draw
  const lo = Math.min(...temps) - 0.5, hi = Math.max(...temps) + 0.5;
  const hrs = s.map(p => p.hr).filter((v): v is number => v != null);
  const hlo = Math.min(...hrs, 200) - 2, hhi = Math.max(...hrs, 0) + 2;
  const X = (t: number) => ((t - x0) / Math.max(1, x1 - x0)) * W;
  const Y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const HY = (v: number) => H - 4 - ((v - hlo) / Math.max(1, hhi - hlo)) * 22;
  const path = (key: "target" | "measured") => s.filter(p => p[key] != null).map((p, i) => `${i ? "L" : "M"}${X(p.ts).toFixed(1)},${Y(p[key]!).toFixed(1)}`).join(" ");
  const hrPath = s.filter(p => p.hr != null).map((p, i) => `${i ? "L" : "M"}${X(p.ts).toFixed(1)},${HY(p.hr!).toFixed(1)}`).join(" ");
  const ticks: number[] = [];
  for (let t = Math.ceil(x0 / 7200) * 7200; t < x1; t += 7200) ticks.push(t);
  const near = sc.st ? s.reduce((a2, b2) => (Math.abs(b2.ts - sc.st!.t) < Math.abs(a2.ts - sc.st!.t) ? b2 : a2)) : null;
  const deg = (c: number | null) => (c == null ? "—" : `${(units === "F" ? cToF(c) : c).toFixed(1)}°`);
  return (
    <div ref={sc.ref} style={{ position: "relative" }} {...sc.handlers}>
    {near && <div className="scrubtip" style={{ left: sc.tipLeft(sc.st!.px), top: sc.st!.mouse ? 8 : -64 }}>
      <div className="row"><b>{clock(near.ts, tz)}</b><span className="mute">{near.occupied > 0.5 ? "in bed" : "out of bed"}</span></div>
      <span>plan <b>{deg(near.target)}</b> · bed <b>{deg(near.measured)}</b></span>
      <span>heart rate <b>{near.hr != null ? Math.round(near.hr) : "—"}</b> bpm</span></div>}
    <svg viewBox={`0 0 ${W} ${H + 12}`} style={{ width: "100%", touchAction: "pan-y" }} role="img" aria-label="Bed temperature and heart rate through the night. Slide along it to see values.">
      {(() => { const runs: [number, number][] = []; let st: number | null = null;
        s.forEach((p, i) => { if (p.occupied > 0.5 && st == null) st = p.ts; if ((p.occupied <= 0.5 || i === s.length - 1) && st != null) { runs.push([st, p.ts]); st = null; } });
        return runs.map(([a, b2], i) => <rect key={i} x={X(a)} y={T} width={Math.max(1, X(b2) - X(a))} height={H - T - B} fill="var(--acc)" opacity=".07" />); })()}
      <path d={path("measured")} fill="none" stroke="var(--text)" strokeOpacity=".35" strokeWidth="1" />
      <path d={path("target")} fill="none" stroke="var(--acc)" strokeWidth="2" strokeLinejoin="round" />
      <path d={hrPath} fill="none" stroke="var(--warm)" strokeOpacity=".7" strokeWidth="1" />
      <text x="0" y={H - 26} fontSize="9" fill="var(--faint)">heart rate</text>
      <text x={W} y={T + 8} fontSize="9" textAnchor="end" fill="var(--faint)">{tempNum(hi - 0.5, units)}°</text>
      <text x={W} y={H - B - 2} fontSize="9" textAnchor="end" fill="var(--faint)">{tempNum(lo + 0.5, units)}°</text>
      {ticks.map(t => <text key={t} x={X(t)} y={H + 10} fontSize="9" textAnchor="middle" fill="var(--faint)">{new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", timeZone: tz })}</text>)}
      {near && <line x1={X(near.ts)} x2={X(near.ts)} y1={T} y2={H - 2} stroke="var(--text)" strokeOpacity=".45" />}
    </svg>
    </div>
  );
}

const durText = (min: number) => min < 60 ? `${min} min` : `${Math.floor(min / 60)} h${min % 60 ? ` ${min % 60} min` : ""}`;

function momentText(m: Moment, units: "F" | "C"): [string, string, string?] {
  const [icon, title, sub] = momentBase(m, units);
  const from = m.kind === "rhythm_changed" || m.kind === "home_changed" || m.kind === "pet" ? null : sourceText(m.source);
  return [icon, title, [sub, from].filter(Boolean).join(" · ") || undefined];
}

function momentBase(m: Moment, units: "F" | "C"): [string, string, string?] {
  switch (m.kind) {
    case "rhythm_started": return ["◐", "Got in bed · rhythm started", m.fitted ? "later than usual: fitted into the time before your warm wake" : undefined];
    case "room_cold": return ["·", m.text ?? "The room was cold", "the plan wasn't changed for it"];
    case "active": return ["·", m.text ?? "Active, not sleeping yet", m.minutes ? `${durText(m.minutes)} · not counted as sleep` : "not counted as sleep"];
    case "out_of_bed": return ["○", `Out of bed ${m.minutes} min`, m.corrected ? "corrected by you" : "your side held its temperature"];
    case "corrected_stretch": return ["✎", m.who === "pet" ? "That was the pet" : "That was me", "corrected by you"];
    case "hot_flash": return ["❄", `Hot flash ${Math.round(m.minutes ?? 15)} min${m.extended ? ` (+${m.extended * 5})` : ""}`, m.stopped_at ? "stopped early" : undefined];
    case "cold_flash": return ["☼", `Cold flash ${Math.round(m.minutes ?? 15)} min${m.extended ? ` (+${m.extended * 5})` : ""}`];
    case "adjust": {
      const net = m.temp_c != null ? `set to ${tempNum(m.temp_c, units)}°` : deltaText(m.delta_c ?? 0, units);
      const dir = (m.delta_c ?? 0) < 0 ? "cooler" : "warmer";
      // a burst of taps is one entry: say which stage, how many taps, and what they added up to
      const what = m.phase_name ? `${m.phase_name} stage made ${dir}` : `Made it ${dir}`;
      const taps = m.count && m.count > 1 ? `${m.count} taps, ` : "";
      return ["±", what, m.temp_c != null ? `${taps}${net}` : `${taps}${net} in all`];
    }
    case "adjust_cleared": return ["↺", "Went back to the rhythm"];
    case "tonight_skip": return ["○", "Turned off for the night"];
    case "tonight_resume": return ["●", "Turned back on"];
    case "tonight_pause": return ["‖", "Paused for an hour"];
    case "tonight_start_now": return ["▸", "Started manually"];
    case "rhythm_changed": return ["✎", (m.count as number) > 1 ? `Rhythm edited ${m.count}×` : "Rhythm edited"];
    case "rhythm_restarted": return ["◐", "Back in bed · rhythm picked up again"];
    case "home_changed": return m.home === false ? ["↗", "Left home", m.why || "pre-cool waits while you're out"]
      : m.home === true ? ["⌂", "Back home", m.why || undefined] : ["·", "Home or away not known", m.why || undefined];
    case "pet": return ["∴", m.text ?? "Looked like a pet on the bed",
      [m.minutes ? `for ${durText(m.minutes)}` : "", m.with_person ? "its moving about isn't counted as yours" : "didn't start or end your night"].filter(Boolean).join(" · ")];
    default: return ["·", m.kind.replace(/_/g, " ")];
  }
}

export default function SleepScreen() {
  const { me, snap, say } = useApp();
  const [nights, setNights] = useState<Night[] | null>(null);
  // "?night=2026-11-06" (from the Today card on Tonight) opens that night instead of the newest
  const loc = useLocation();
  const wanted = /^\d{4}-\d{2}-\d{2}$/.test(new URLSearchParams(loc.search).get("night") ?? "") ? new URLSearchParams(loc.search).get("night") : null;
  const [date, setDate] = useState<string | null>(wanted);
  useEffect(() => { if (wanted) setDate(wanted); }, [wanted]);
  const [night, setNight] = useState<Night | null>(null);
  const [stageSrc, setStageSrc] = useState<string | null>(null);
  const [allStages, setAllStages] = useState<StageSrc[]>([]);
  const minute = snap ? Math.floor(snap.now / 120) : 0;
  const [pets, setPets] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => { api.get<{ pets: { id: string; name: string }[] }>("/api/pets").then(r => setPets(r.pets)).catch(() => setPets([])); }, []);
  // before tonight has anything, open on last night
  useEffect(() => { if (me) api.get<Night[]>(`/api/people/${me.id}/nights?count=7`).then(n => { setNights(n); setDate(d => d ?? (n[0]?.has_data || !n[1]?.has_data ? n[0]?.date : n[1].date) ?? null); }).catch(() => {}); }, [me?.id, minute]);
  useEffect(() => { if (me && date) api.get<Night>(`/api/people/${me.id}/night?date=${date}`).then(setNight).catch(() => setNight(null)); }, [me?.id, date, minute]);
  useEffect(() => { if (me && date) api.get<StageSrc[]>(`/api/people/${me.id}/stages?date=${date}`).then(setAllStages).catch(() => setAllStages([])); }, [me?.id, date, minute]);
  if (!me || !snap) return null;
  const u = me.settings.units;
  const n = night;
  const rate = async (patch: { temp?: string; note?: string; rested?: number }) => {
    if (!n) return;
    try {
      await api.post(`/api/people/${me.id}/feedback`, { date: n.date, ...patch });
      setNight({ ...n, feedback: { ...(n.feedback ?? {}), ...patch } });
      say(patch.note !== undefined ? "Saved. Autopilot and the AI review will read it." : "Thanks, noted for tuning");
    } catch { say("Couldn't save that"); }
  };

  return (
    <div className="screen">
        <PetQuestion person={me.id} />
        <MorningSuggestions pid={me.id} units={u} say={say} />
        <GuideCards pid={me.id} say={say} />
      <div className="k">Sleep</div>
      {(() => {
        const list = nights ?? [];
        const idx = list.findIndex(x => x.date === date);
        const older = idx >= 0 && idx < list.length - 1 ? list[idx + 1] : null;
        const newer = idx > 0 ? list[idx - 1] : null;
        const week = [...list].slice(0, 7).reverse();
        const maxMin = Math.max(480, ...week.map(x => x.in_bed_min ?? 0));
        return <>
          <div className="daynav">
            <button aria-label="Previous night" data-key="arrowleft" disabled={!older} onClick={() => older && setDate(older.date)}>‹</button>
            <div style={{ textAlign: "center" }}><h1 style={{ fontSize: 24 }}>{n?.in_progress ? "Tonight" : n?.label ?? "…"}</h1>
              <span className="mute" style={{ fontSize: 12.5 }}>{n?.in_progress ? "so far" : "night of"}</span></div>
            <button aria-label="Next night" data-key="arrowright" disabled={!newer} onClick={() => newer && setDate(newer.date)}>›</button>
          </div>
          {week.length > 1 && <div className="week" role="tablist" aria-label="Last 7 nights">
            {week.map(x => { const d = new Date(x.date + "T12:00:00"); const sel = x.date === date;
              return <button key={x.date} role="tab" aria-selected={sel} className={sel ? "on" : ""} onClick={() => setDate(x.date)} title={x.label}>
                <span className="bar"><i style={{ height: `${x.has_data ? Math.max(6, ((x.in_bed_min ?? 0) / maxMin) * 100) : 0}%` }} /></span>
                <span className="d">{x.in_progress ? "Now" : d.toLocaleDateString([], { weekday: "narrow" })}</span>
                <span className="h">{x.has_data ? hm(x.in_bed_min) : "—"}</span></button>; })}
          </div>}
        </>;
      })()}
      {!n ? <p className="mute">Loading…</p> : !n.has_data ? (
        <div className="card"><p>Nothing recorded for this night yet.</p><p className="mute" style={{ fontSize: 13 }}>Once you're in bed, this fills in as the night goes.</p></div>
      ) : (<>
        <div className="tcols">
        <section className="tcol">
        <p style={{ fontSize: 15 }}>
          In bed {clock(n.in_bed_at, snap.tz)}{!n.in_progress && n.out_at ? ` to ${clock(n.out_at, snap.tz)}` : ""} · <span className="mute">{hm(n.in_bed_min)}</span>
        </p>
        <div className="stats">
          <div className="stat"><span className="v">{hm(n.settled_min)}</span><span className="l">settled (est.)<Tip label="How settled is estimated" text={`"Settled" and the bed's stage estimate come from its sensors (stillness and heart rate), not medical measurements.`} /></span></div>
          <div className="stat"><span className="v">{hm(n.time_to_settle_min)}</span><span className="l">to settle</span></div>
          <div className="stat"><span className="v">{n.times_up ?? 0}</span><span className="l">times up{n.up_minutes ? ` · ${n.up_minutes} min` : ""}</span></div>
          <div className="stat"><span className="v">{n.hr_avg ?? "—"}</span><span className="l">avg bpm · low {n.hr_low ?? "—"}</span></div>
          <div className="stat"><span className="v">{n.br_avg ?? "—"}</span><span className="l">breaths/min</span></div>
          <div className="stat"><span className="v">{n.tracking_c != null ? `±${(u === "F" ? n.tracking_c * 1.8 : n.tracking_c).toFixed(1)}°` : "—"}</span><span className="l">bed vs plan, on average</span></div>
        </div>
        {allStages.length > 0 && (() => { const src = allStages.find(x => x.id === stageSrc) ?? allStages[0]; const t = src.totals; const tot = Object.values(t).reduce((p, q) => p + q, 0) || 1;
          return <Fold id="sleep.stages" title="Sleep stages" defaultOpen
            summary={`deep ${hm(t.deep ?? 0)} · REM ${hm(t.rem ?? 0)}`}><div className="card">
            {src.kind === "estimate" && <span className="faint" style={{ fontSize: 12 }}>Estimated from the bed<Tip label="About stage sources" text="Stages from a watch or ring appear here once connected, and you can switch between sources." /></span>}
            <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden" }}>{["deep", "light", "rem", "awake"].map(k => <span key={k} style={{ width: `${(t[k] ?? 0) / tot * 100}%`, background: STAGE_COLOR[k] }} />)}</div>
            <div className="stagekey">{["deep", "light", "rem", "awake"].map(k => <span key={k}><i style={{ background: STAGE_COLOR[k] }} />{k === "rem" ? "REM" : k[0].toUpperCase() + k.slice(1)}<b>{hm(t[k] ?? 0)}</b></span>)}</div>
            <Hypnogram src={src} tz={snap.tz} />
            {allStages.length > 1 && <div className="chips">{allStages.map(x => <button key={x.id} className="chip" aria-pressed={x.id === src.id} onClick={() => setStageSrc(x.id)}>{x.kind === "estimate" ? "Bed estimate" : x.label.split(" · ")[0]}</button>)}</div>}
            <span className="faint" style={{ fontSize: 11.5 }}>From {src.label}{src.agree_with_first != null ? ` · agrees ${Math.round(src.agree_with_first * 100)}% with ${allStages[0].label.split(" · ")[0]}` : ""}</span>
          </div></Fold>; })()}
        {(n.series?.length ?? 0) >= 3 && <div className="card"><NightChart n={n} units={u} tz={snap.tz} />
          <div className="mute" style={{ fontSize: 12 }}><span style={{ color: "var(--acc)" }}>━</span> plan · <span>━</span> bed · <span style={{ color: "var(--warm)" }}>━</span> heart rate · shaded = in bed</div></div>}
        </section>
        <section className="tcol">
        {n.ring && <RingCard ring={n.ring} units={u} date={n.date} />}
        {(n.naps ?? []).length > 0 && <NapsCard naps={n.naps!} nightMin={n.in_progress ? null : n.night_asleep_min ?? null} />}
        {!n.in_progress && <NightFix key={n.date} person={me.id} date={n.date} tz={snap.tz} pets={pets}
          ups={n.moments.filter(m => m.kind === "out_of_bed" && !m.corrected).map(m => ({ ts: m.ts, minutes: m.minutes }))}
          onChanged={() => api.get<Night>(`/api/people/${me.id}/night?date=${n.date}`).then(setNight).catch(() => {})} />}
        {!n.in_progress && (
          <div className="card">
            <span>{n.voice?.checkin ?? "How was the temperature?"}</span>
            <div className="chips">{[["cold", "Too cold"], ["right", "Just right"], ["warm", "Too warm"]].map(([k, l]) =>
              <button key={k} className="chip" aria-pressed={n.feedback?.temp === k} onClick={() => rate({ temp: k })}>{l}</button>)}</div>
            <span style={{ marginTop: 6 }}>How rested do you feel?</span>
            <div className="chips">{[1, 2, 3, 4, 5].map(k => <button key={k} className="chip" aria-pressed={n.feedback?.rested === k} onClick={() => rate({ rested: k })}>{["Wrecked", "Tired", "OK", "Good", "Great"][k - 1]}</button>)}</div>
            <NoteBox key={n.date} initial={n.feedback?.note ?? ""} onSave={note => rate({ note })} />
          </div>
        )}
        <Fold id="sleep.moments" title="The night" defaultOpen summary={n.moments.length ? `${n.moments.length} moment${n.moments.length === 1 ? "" : "s"}` : "a quiet night"}>
        <div className="card" style={{ gap: 0 }}>
          {n.moments.length ? n.moments.map((m, i) => { const [g, t, sub] = momentText(m, u); return (
            <div className="moment" key={i}><span className="tm">{clock(m.ts, snap.tz)}</span><span aria-hidden>{g}</span><span>{t}{sub && <small>{sub}</small>}</span></div>); })
            : <p className="mute">A quiet night: nothing changed.</p>}
        </div>
        </Fold>
        </section>
        </div>
      </>)}
    </div>
  );
}


function NoteBox({ initial, onSave }: { initial: string; onSave: (note: string) => void }) {
  const [v, setV] = useState(initial);
  return (
    <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
      <span>Anything else? <small className="mute">In your own words: "woke up sweaty around 3", "feet were cold", "slept great".</small></span>
      <textarea value={v} maxLength={2000} rows={3} onChange={e => setV(e.target.value)} placeholder="How do you feel?" style={{ background: "var(--bg)", color: "var(--text)", border: "1px solid var(--line2)", borderRadius: 10, padding: "8px 10px", font: "inherit", resize: "vertical" }} />
      {v !== initial && <button className="btn pri" style={{ justifySelf: "start" }} onClick={() => onSave(v)}>Save note</button>}
    </div>
  );
}


function MorningSuggestions({ pid, units, say }: { pid: string; units: "F" | "C"; say: (m: string) => void }) {
  const [data, setData] = useState<import("../types").Suggestions | null>(null);
  const load = () => api.get<import("../types").Suggestions>(`/api/people/${pid}/autopilot/suggestions`).then(setData).catch(() => {});
  useEffect(() => { load(); }, [pid]);
  if (!data || data.items.length === 0) return null;
  const f = (c?: number) => c == null ? "" : `${Math.round(units === "F" ? c * 9 / 5 + 32 : c)}°`;
  return (
    <div className="card" style={{ borderColor: "color-mix(in srgb,var(--acc) 45%,var(--line))" }}>
      <span className="k">Suggestion</span>
      {data.items.slice(0, 2).map(s => (
        <div key={s.id} style={{ display: "grid", gap: 6, paddingTop: 4 }}>
          <b style={{ fontWeight: 500, fontSize: 15 }}>{s.title}</b>
          <span className="mute" style={{ fontSize: 13 }}>{s.detail} {s.proposal.from != null && <>({f(s.proposal.from)} → {f(s.proposal.to)})</>}</span>
          <div className="chips">
            <button className="btn pri" onClick={async () => { try { await api.post(`/api/people/${pid}/insights/apply`, { proposal: s.proposal, insight_id: s.id, source: "suggestion" }); say("Added to your rhythm"); load(); } catch (e) { say(`Couldn't: ${(e as Error).message}`); } }}>Add to my rhythm</button>
            <button className="chip" onClick={async () => { try { await api.post(`/api/people/${pid}/autopilot/suggestions/${s.id}/dismiss`, { forever: false }); load(); } catch (e) { say(`Couldn't: ${(e as Error).message}`); } }}>Not now</button>
            {s.kind === "manual" && <button className="chip" onClick={async () => { try { await api.post(`/api/people/${pid}/autopilot/suggestions/${s.id}/dismiss`, { forever: true }); say("Won't suggest your own changes again. Turn it back on in Studio → Autopilot."); load(); } catch (e) { say(`Couldn't: ${(e as Error).message}`); } }}>Don't ask again</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

interface GuideItem { id: string; kind?: string; title: string; detail: string; proposal: { rhythm_id: string; schedule?: string; from?: string | number; to: string | number } | null; applied?: unknown }

/** Tuning-guide changes (bedtime / wake that match how your nights really go). Only ones with a change to make;
 *  "Not now" hides it on this phone until it comes back with new evidence (a different suggested time). */
function GuideCards({ pid, say }: { pid: string; say: (m: string) => void }) {
  const [items, setItems] = useState<GuideItem[]>([]);
  const key = `et-guide-hidden:${pid}`;
  const hidden = (): string[] => { try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch { return []; } };
  const [hide, setHide] = useState<string[]>(hidden);
  const load = () => api.get<GuideItem[]>(`/api/people/${pid}/insights`).then(r => setItems(r.filter(i => i.kind === "guide" && i.proposal?.schedule))).catch(() => {});
  useEffect(() => { load(); setHide(hidden()); }, [pid]);
  const sig = (i: GuideItem) => `${i.id}:${i.proposal?.to}`;
  const shown = items.filter(i => !hide.includes(sig(i))).slice(0, 2);
  if (!shown.length) return null;
  return (
    <div className="card">
      <span className="k">Tuning guide</span>
      {shown.map(i => (
        <div key={i.id} style={{ display: "grid", gap: 6, paddingTop: 4 }}>
          <b style={{ fontWeight: 500, fontSize: 15 }}>{i.title}</b>
          <span className="mute" style={{ fontSize: 13 }}>{i.detail}</span>
          <div className="chips">
            <button className="btn pri" onClick={async () => { try { await api.post(`/api/people/${pid}/insights/apply`, { proposal: i.proposal, insight_id: i.id, source: "guide" }); say(`${i.proposal!.schedule === "bedtime" ? "Bedtime" : "Wake"} is now ${hm12(String(i.proposal!.to))}`); load(); } catch (e) { say(`Couldn't: ${(e as Error).message}`); } }}>
              Change to {hm12(String(i.proposal!.to))}</button>
            <button className="chip" onClick={() => { const n = [...hide, sig(i)]; setHide(n); try { localStorage.setItem(key, JSON.stringify(n.slice(-40))); } catch { /* private mode */ } }}>Not now</button>
          </div>
        </div>
      ))}
    </div>
  );
}
