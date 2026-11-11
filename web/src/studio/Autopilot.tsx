import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import Fold from "../components/Fold";
import ShowMore from "../components/ShowMore";
import Tip from "../components/Tip";
import LeftOutNights from "../components/LeftOutNights";
import { HelpLink } from "./Help";
import { useApp } from "../ctx";
import { useAuth } from "../auth";
import { clock, cToF } from "../format";
import type { AutopilotPolicy, Band, Baseline, Learning, Suggestions } from "../types";

interface Reason { signal: string; label: string; value: string; detail: string; push: number }
interface Decision { ts: number; phase: string | null; score: number; threshold: number; direction: "cooler" | "warmer" | "none"; delta_c: number;
  reasons: Reason[]; status: "watching" | "suggested" | "applied" | "blocked" | "learning"; blocked_by: string[]; target_before: number | null; target_after: number | null;
  signals?: Record<string, number | string | null> }
interface Meta { signals: { id: string; label: string }[]; sensitivity: Record<string, number>; ai: boolean; model: string | null }
interface Status { policy: AutopilotPolicy; state: string; live: Decision | null; tonight: Decision[]; has_skin: boolean; learning?: Learning; baseline?: Baseline | null;
  current?: { bed_c: number | null; hr: number | null; skin: number | null } }
interface Moment { ts: number; direction: "cooler" | "warmer"; delta_c: number; phase: string | null; missed: boolean; why: string }
interface Recheck { ts: number; decisions: Decision[]; new_findings: number; used_skin: boolean; text?: string; moments?: Moment[]; would_change?: number; missed_live?: number }
interface NightData { date: string; start: number; end: number; wake: number | null; phases: { id: string; name: string; start: number; temp_c: number }[];
  rows: { ts: number; target: number | null; measured: number | null; hr: number | null; status: number | null; occupied: number | null; phase: string | null }[];
  skin: [number, number][]; room: [number, number][]; humidity: [number, number][]; series: Record<string, [number, number][]>;
  decisions: Decision[]; feel: { ts: number; feel: string; note?: string }[]; flashes: { ts: number; kind: string }[]; manual: { ts: number; delta_c?: number; temp_c?: number }[];
  feedback: { temp?: string; note?: string; rested?: number } | null; recheck: Recheck | null }
interface Suggestion { id: string; kind?: string; title: string; detail: string; proposal: { rhythm_id: string; phase_id: string; field: string; from?: number; to: number } }
interface Trend { id: string; title: string; detail: string; evidence: string[] }
interface ImportRow { ts: number; device: string; metric: string; received: number; stored: number; rejected: Record<string, number>; shift_applied_s: number; warnings: string[]; notes: string[];
  nights?: { date: string; samples?: number; coverage_in_bed?: number | null; lag_min?: number | null; match?: number | null }[] }
const METRIC: Record<string, string> = { hr: "heart rate", skin_temp: "skin temperature", hrv: "heart rate variability", resp_rate: "breathing rate", spo2: "blood oxygen",
  resting_hr: "resting heart rate", body_temp: "body temperature", steps: "steps", active_kcal: "active calories", exercise_min: "exercise", caffeine_mg: "caffeine", weight_kg: "weight" };
/** One sync (all metrics sent within a couple of minutes) as plain sentences. Tonight's night is still going, so
 *  "covers 0% of the time in bed" is expected and not shown; a weak heart-rate match is a note, not an error. */
function syncSummary(rows: ImportRow[], today: string): { what: string; notes: string[] } {
  const n = (x: number) => x.toLocaleString();
  const fresh = rows.filter(r => r.stored > 0), same = rows.filter(r => r.stored === 0 && (r.rejected.duplicate ?? 0) > 0);
  const parts: string[] = [];
  if (fresh.length) parts.push(`New: ${fresh.map(r => `${n(r.stored)} ${METRIC[r.metric] ?? r.metric}`).join(", ")}`);
  if (same.length) parts.push(`already had: ${same.map(r => METRIC[r.metric] ?? r.metric).join(", ")}`);
  if (!parts.length) parts.push("Nothing new");
  const notes: string[] = [];
  for (const r of rows) {
    if (r.shift_applied_s) notes.push(`${METRIC[r.metric] ?? r.metric}: the device's clock was ${Math.abs(Math.round(r.shift_applied_s / 60))} min ${r.shift_applied_s > 0 ? "behind" : "ahead"}, so the times were corrected.`);
    if (r.rejected.future) notes.push(`${n(r.rejected.future)} ${METRIC[r.metric] ?? r.metric} reading${r.rejected.future === 1 ? "" : "s"} were stamped a little in the future (the phone's clock runs ahead) and skipped.`);
    for (const w of r.warnings) {
      if (w.startsWith(today) || /covers only/.test(w) && /^\d{4}-\d{2}-\d{2}/.test(w) && w.slice(0, 10) >= today) continue;
      if (/no clear match/.test(w)) notes.push(`${w.slice(0, 10)}: the watch's heart rate didn't line up clearly with the bed's that night (usual when the watch wasn't worn in bed), so its timing was left as is.`);
      else notes.push(w);
    }
  }
  return { what: parts.join(" · "), notes: [...new Set(notes)] };
}

const STATUS_TEXT: Record<string, string> = { suggested: "Would change", applied: "Changed", blocked: "Held back", watching: "Watching", learning: "Learning (noted only)" };
const STATUS_COLOR: Record<string, string> = { suggested: "var(--acc)", applied: "var(--good)", blocked: "var(--warm)", watching: "var(--mute)", learning: "var(--faint)" };
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const TIPS = {
  learning: "Learning nights: for the first nights it only watches, to learn what a normal night looks like for you (how much you move, your settled heart rate, your skin temperature). It changes and suggests nothing until then.",
  sensitivity: "How much evidence it needs. Low waits for strong, agreeing signals; high reacts to smaller hints. Recommended: medium.",
  trust: "Trust (weighting): how much each source counts toward too warm or too cold. 0 ignores it, 1 is normal, 2 counts double. Your own taps always count in full. Recommended: all at 1 until you've seen a few weeks of nights.",
  cadence: "Cadence: how often the phone shows saved-up suggestions. Studio always shows them. A suggestion only appears once a pattern shows on several nights.",
  replay: "Runs Autopilot over the recorded night as if these were the settings. Nothing changes; use it to tune before saving.",
  limits: "Act mode stays inside these. In Suggest mode they shape what it would have done.",
  wearable: "Every import is lined up with the bed first: times are normalised, duplicates dropped, and a watch whose clock is off (or an export an hour out) is detected from heart rate and corrected.",
  ai: "Hands the night to the AI model (or the built-in analysis): what Autopilot saw, what was said in the morning, the room and any watch data. It suggests; you decide.",
  score: "Each signal pushes the score toward warm or cold. Past the line on either side it would act (or suggest).",
  band: "Your normal range: the usual spread of your readings over the learning nights (the middle 80%, with a little give). While everything stays inside it, Autopilot makes no changes and no suggestions. It only steps in when something stays outside it for a while, or when you tap too hot / too cold yourself.",
  recheck: "Runs Autopilot over this finished night again, using everything known now (for example watch data that arrived in the morning), and shows what it would have done in hindsight. Nothing is changed.",
  recal: "Forgets what Autopilot has learned about this person and starts the learning nights again. Useful after a new mattress, a new season or a health change.",
};
const TRUST = [
  ["bed", "The bed's own sensors", "Restlessness, heart rate and how hard the pad is working."],
  ["wearable", "Watch or ring", "Skin temperature and heart rate from a wearable."],
  ["room", "Room sensor", "Bedroom temperature and humidity."],
] as const;
/** When the phone next shows suggestions: weekly = the next chosen weekday, monthly = the first one of a month.
 *  Today counts if it is that day. Null for every morning / off. */
function nextShow(cadence: string, weekday: number, now: number): Date | null {
  if (cadence !== "weekly" && cadence !== "monthly") return null;
  const d = new Date(now * 1000);
  const wd = (x: Date) => (x.getDay() + 6) % 7;                 // 0 = Monday, like the server
  for (let i = 0; i < 70; i++) {
    const c = new Date(d.getFullYear(), d.getMonth(), d.getDate() + i, 12);
    if (wd(c) === weekday && (cadence === "weekly" || c.getDate() <= 7)) return c;
  }
  return null;
}
function trustText(v: number): string {
  return v === 0 ? "ignored" : v < 0.75 ? "counts less" : v <= 1.25 ? "normal" : v < 1.75 ? "counts more" : "counts double";
}
const MOVING = new Set([4, 6, 7]);

export default function Autopilot() {
  const { snap, say } = useApp();
  const auth = useAuth();
  const [pid, setPid] = useState<string | null>(null);
  const [recal, setRecal] = useState<"idle" | "confirm" | "busy">("idle");
  const [rechecking, setRechecking] = useState(false);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [st, setSt] = useState<Status | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [night, setNight] = useState<NightData | null>(null);
  const [sugs, setSugs] = useState<Suggestions | null>(null);
  const [trends, setTrends] = useState<Trend[]>([]);
  const [imports, setImports] = useState<ImportRow[]>([]);
  const [pol, setPolState] = useState<AutopilotPolicy | null>(null);
  const polRef = useRef<AutopilotPolicy | null>(null);
  const setPol = (p: AutopilotPolicy | null) => { polRef.current = p; setPolState(p); };
  const saveTimer = useRef<number | undefined>(undefined);
  const [saving, setSaving] = useState<string | null>(null);
  const [dirty, setDirtyState] = useState(false);
  // The 30-second refresh runs from an old closure: read "has unsaved edits" from a ref so it never wipes them.
  const dirtyRef = useRef(false);
  const setDirty = (v: boolean) => { dirtyRef.current = v; setDirtyState(v); };
  const [replay, setReplay] = useState<{ decisions: Decision[]; summary: Record<string, number> } | null>(null);
  const [rpSens, setRpSens] = useState<string>("medium");
  const [rpSignals, setRpSignals] = useState<string[] | null>(null);
  const [review, setReview] = useState<{ answer: string; proposal: Suggestion["proposal"] | null; model: string | null; ts: number } | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [sel, setSel] = useState<Decision | null>(null);
  const person = snap?.people.find(p => p.id === (pid ?? snap.people[0]?.id));
  const u = person?.settings.units ?? "F";
  const deg = (c: number | null | undefined, d = 0) => c == null ? "–" : `${(u === "F" ? cToF(c) : c).toFixed(d)}°`;
  const dd = (c: number) => `${c > 0 ? "+" : "−"}${Math.abs(u === "F" ? c * 9 / 5 : c).toFixed(u === "F" ? 0 : 1)}°`;

  const loadAll = () => {
    if (!person) return;
    api.get<Status>(`/api/people/${person.id}/autopilot`).then(s => { setSt(s); if (!dirtyRef.current) { setPol(s.policy); setRpSens(s.policy.sensitivity); } }).catch(() => {});
    api.get<Suggestions>(`/api/people/${person.id}/autopilot/suggestions?all=true`).then(setSugs).catch(() => {});
    api.get<Trend[]>(`/api/people/${person.id}/autopilot/trends`).then(setTrends).catch(() => {});
    api.get<ImportRow[]>(`/api/people/${person.id}/imports`).then(r => setImports([...r].reverse())).catch(() => {});
  };
  useEffect(() => { api.get<Meta>("/api/autopilot/meta").then(setMeta).catch(() => {}); }, []);
  useEffect(() => { setDirty(false); setReplay(null); setReview(null); loadAll(); const t = setInterval(loadAll, 30000); return () => clearInterval(t); }, [person?.id]);
  useEffect(() => {
    if (!person) return;
    api.get<NightData>(`/api/people/${person.id}/autopilot/night${date ? `?date=${date}` : ""}`).then(n => { setNight(n); if (!date) setDate(n.date); }).catch(() => setNight(null));
    api.get<typeof review>(`/api/people/${person.id}/autopilot/ai-review${date ? `?date=${date}` : ""}`).then(r => setReview(r && (r as { answer?: string }).answer ? r : null)).catch(() => {});
  }, [person?.id, date]);
  if (!snap || !person || !pol || !meta) return null;

  // Settings save themselves (like the phone): each change waits a moment for the next one, then saves.
  const save = async (p: AutopilotPolicy = polRef.current ?? pol) => {
    window.clearTimeout(saveTimer.current);
    setSaving("Saving…");
    try { await api.put(`/api/people/${person.id}/settings`, { ...person.settings, autopilot: p }); setDirty(false); setSaving("Saved"); window.setTimeout(() => setSaving(x => x === "Saved" ? null : x), 1500); loadAll(); }
    catch (e) { setSaving(null); say(`Couldn't save: ${(e as Error).message}`); }
  };
  const upd = (patch: Partial<AutopilotPolicy>, delay = 600) => {
    const next = { ...(polRef.current ?? pol), ...patch };
    polRef.current = next;
    setPol(next); setDirty(true);
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => void save(next), delay);
  };
  const setMode = (m: AutopilotPolicy["mode"]) => upd({ mode: m }, 0);
  const shiftDate = (n: number) => { if (!date) return; const d = new Date(date + "T12:00:00"); d.setDate(d.getDate() + n); setDate(d.toISOString().slice(0, 10)); setReplay(null); setSel(null); };
  const apply = async (prop: Suggestion["proposal"], id?: string) => {
    try { const r = await api.post<{ applied: string }>(`/api/people/${person.id}/insights/apply`, { proposal: prop, insight_id: id, source: "autopilot" }); say(`Applied: ${r.applied}`); loadAll(); }
    catch (e) { say(`Couldn't apply: ${(e as Error).message}`); }
  };
  const runReplay = async () => {
    try { setReplay(await api.post(`/api/people/${person.id}/autopilot/replay`, { date, policy: { sensitivity: rpSens, signals: rpSignals ?? pol.signals } })); }
    catch (e) { say((e as Error).message); }
  };
  const runReview = async () => {
    setReviewing(true);
    try { setReview(await api.post(`/api/people/${person.id}/autopilot/ai-review`, { date })); } catch (e) { say((e as Error).message); }
    setReviewing(false);
  };
  const live = st?.live;
  const lr = st?.learning;
  const canRecal = !auth.state.enabled || auth.isOwner || auth.me?.person === person.id;
  const doRecal = async () => {
    setRecal("busy");
    try { await api.post(`/api/people/${person.id}/autopilot/recalibrate`); setDirty(false); say(`Starting over: learning ${person.name}'s nights again`); loadAll(); }
    catch (e) { say(`Couldn't start over: ${(e as Error).message}`); }
    setRecal("idle");
  };
  const recheck = async () => {
    if (!date) return;
    setRechecking(true);
    try { await api.post(`/api/people/${person.id}/autopilot/recheck`, { date }); setNight(await api.get<NightData>(`/api/people/${person.id}/autopilot/night?date=${date}`)); }
    catch (e) { say(`Couldn't re-check: ${(e as Error).message}`); }
    setRechecking(false);
  };
  const cadenceText = pol.suggest_cadence === "weekly" ? `shown on the phone every ${DAYS[pol.suggest_weekday]}`
    : pol.suggest_cadence === "monthly" ? `shown on the phone the first ${DAYS[pol.suggest_weekday]} of each month`
    : pol.suggest_cadence === "off" ? "not shown on the phone" : "shown on the phone each morning";
  const modeText = pol.mode === "off" ? "Off. It doesn't watch or change anything."
    : pol.mode === "suggest" ? "Suggest. It watches each night, notes what it would change and why, and nothing changes. Suggestions show up here in the morning."
    : "Act. It makes small changes overnight within the limits below, and explains each one. Pressing + or − yourself always wins.";
  const tonightCounts = (st?.tonight ?? []).reduce<Record<string, number>>((a, d) => { a[d.status] = (a[d.status] ?? 0) + 1; return a; }, {});

  return (
    <>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div><div className="k">Analyze</div><h1 style={{ fontSize: 32 }}>Autopilot</h1>
          <p className="mute" style={{ fontSize: 13, maxWidth: 680 }}>Notices when you look too warm or too cold, from your own taps, a watch or ring, restlessness, heart rate, the bed and the room. Every decision shows what it saw and why. <HelpLink to="autopilot" label="How Autopilot works" /></p></div>
        <div className="pills">{snap.people.map(p => <button key={p.id} className="pill" aria-pressed={p.id === person.id} onClick={() => { setPid(p.id); setDate(null); setSel(null); }}>{p.name}</button>)}</div>
      </div>

      <div className="grid2">
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div className="box">
            <div className="row"><span className="k">Mode</span>
              <div className="seg" style={{ flex: "0 1 300px", minWidth: 0 }}>{(["off", "suggest", "act"] as const).map(m => <button key={m} aria-pressed={pol.mode === m} onClick={() => setMode(m)}>{m[0].toUpperCase() + m.slice(1)}</button>)}</div></div>
            <span className="mute" style={{ fontSize: 13 }}>{modeText}</span>
            {pol.mode === "act" && <span style={{ fontSize: 12.5, color: "var(--warm)" }}>Tip: run Suggest for a week or two first and check that its suggestions look right before letting it act.</span>}
          </div>

          {lr && <div className="box">
            <div className="row"><span className="k">Learning your nights<Tip label="What are learning nights?" text={TIPS.learning} /></span>
              <span className="chip" style={{ cursor: "default", color: lr.done ? "var(--good)" : "var(--mute)" }}>{lr.done ? (lr.skipped ? "started early" : "done") : `${lr.have} of ${lr.need} nights${lr.paused ? " · paused, away" : ""}`}</span></div>
            {lr.need > 0 && <div className="gl-bar" aria-hidden><i style={{ width: `${Math.min(100, (lr.have / Math.max(1, lr.need)) * 100)}%` }} /></div>}
            <span className="mute" style={{ fontSize: 13 }}>{lr.done
              ? "It knows what a normal night looks like for you, so it can tell an unusual one."
              : `Only watching for the first ${lr.need} nights: nothing is changed or suggested yet.`}</span>
            <LeftOutNights learning={lr} max={7} />
            <Num label="Nights to learn first" v={pol.learn_nights} min={0} max={14} step={1} fmt={v => v === 0 ? "none" : `${v} night${v === 1 ? "" : "s"}`} set={v => upd({ learn_nights: v })} />
            {!lr.done && <div className="row" style={{ justifyContent: "flex-start" }}><button className="btn" onClick={() => upd({ learn_skip: true }, 0)}>Start suggesting now</button></div>}
            {pol.learn_skip && lr.skipped && <div className="row" style={{ justifyContent: "flex-start" }}><button className="pill" onClick={() => upd({ learn_skip: false }, 0)}>Go back to learning</button></div>}
            {st?.baseline?.band && <NormalRange band={st.baseline.band} cur={st.current} deg={deg} u={u} quiet={!!live && !(live.signals?.outside_band as unknown as string[] | undefined)?.length} />}
            {canRecal && (recal === "idle"
              ? <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}><button className="pill" onClick={() => setRecal("confirm")}>Start over: recalibrate</button><Tip label="What does recalibrate do?" text={TIPS.recal} /></div>
              : <div className="box" role="alertdialog" aria-labelledby="recal-t" style={{ borderColor: "var(--warm)", gap: 6 }}>
                <b id="recal-t" style={{ fontWeight: 500 }}>Start over for {person.name}?</b>
                <span style={{ fontSize: 13 }}>This forgets, for {person.name} only:</span>
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, display: "grid", gap: 2 }}>
                  <li>the learned normal range (bed and skin temperature, heart rate, restlessness)</li>
                  <li>how much each source counts (trust goes back to normal)</li>
                  <li>Autopilot's past decisions and suggestions, and what you dismissed</li>
                  <li>re-check results</li>
                  <li>the learning-night count: it learns for {pol.learn_nights || 7} nights again before suggesting anything</li>
                </ul>
                <span className="mute" style={{ fontSize: 12.5 }}>Kept: sleep history, rhythms, all other settings, and {snap.people.filter(x => x.id !== person.id).map(x => x.name).join(" and ") || "other people"}.</span>
                <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
                  <button className="btn" disabled={recal === "busy"} onClick={() => setRecal("idle")}>Cancel</button>
                  <button className="btn pri" disabled={recal === "busy"} onClick={doRecal}>{recal === "busy" ? "Starting over…" : "Yes, start over"}</button></div>
              </div>)}
            {st?.baseline && <div style={{ borderTop: "1px solid var(--line)", paddingTop: 2 }}>
              <Fold id="studio.ap.baseline" className="fold-sub" title={`Your normal · ${st.baseline.nights} nights`}
                summary={`restless ${Math.round(st.baseline.restless_median * 100)}%${st.baseline.hr != null ? ` · ${Math.round(st.baseline.hr)} bpm` : ""}`}>
              <div className="gl-grid" style={{ padding: 0 }}>
                <div className="gl-stat"><span className="k">Restless</span><span className="v num">{Math.round(st.baseline.restless_median * 100)}%</span><small className="mute">of the time, usually</small></div>
                <div className="gl-stat"><span className="k">Restless, busy nights</span><span className="v num">{Math.round(st.baseline.restless_p90 * 100)}%</span><small className="mute">it waits for more than this</small></div>
                {st.baseline.hr != null && <div className="gl-stat"><span className="k">Settled heart rate</span><span className="v num">{Math.round(st.baseline.hr)}</span><small className="mute">beats a minute</small></div>}
                {st.baseline.skin != null && <div className="gl-stat"><span className="k">Skin</span><span className="v num">{deg(st.baseline.skin, 1)}</span><small className="mute">from your wearable</small></div>}
              </div>
              </Fold>
            </div>}
          </div>}

          <div className="box">
            <div className="row"><span className="k">Right now · {person.name}</span>
              <span className="chip" style={{ cursor: "default" }}>{Object.keys(tonightCounts).length ? Object.entries(tonightCounts).map(([k, v]) => `${v} ${STATUS_TEXT[k].toLowerCase()}`).join(" · ") : "nothing yet tonight"}</span></div>
            {!live ? <span className="mute" style={{ fontSize: 13 }}>Not watching: it starts once the night is running and {person.name} is in bed.</span> : <>
              <ScoreBar d={live} />
              <SignalTiles s={live.signals ?? {}} u={u} hasSkin={!!st?.has_skin} />
              <DecisionText d={live} dd={dd} deg={deg} />
            </>}
          </div>

          <div className="box">
            <div className="row"><span className="k">The night</span>
              <span className="pills"><Tip text="The night before"><button className="pill" aria-label="Previous night" onClick={() => shiftDate(-1)}>‹</button></Tip><span className="num" style={{ fontSize: 13, padding: "5px 4px" }}>{date}</span><Tip text="The night after"><button className="pill" aria-label="Next night" onClick={() => shiftDate(1)}>›</button></Tip></span></div>
            {night && night.rows.length > 1 ? <NightChart n={night} u={u} tz={snap.tz} replay={replay?.decisions ?? null} onPick={setSel} /> : <span className="mute" style={{ padding: "30px 0", textAlign: "center" }}>{night && night.start > snap.now - 600 ? "This night hasn't started yet. Pick an earlier night with ‹." : "No bed data for this night."}</span>}
            <div className="chips" style={{ fontSize: 11.5 }}>
              <span className="chip" style={{ cursor: "default" }}><i style={{ display: "inline-block", width: 10, height: 2, background: "var(--acc)", verticalAlign: "middle" }} /> plan</span>
              <span className="chip" style={{ cursor: "default" }}><i style={{ display: "inline-block", width: 10, height: 2, background: "var(--text)", opacity: .5, verticalAlign: "middle" }} /> bed</span>
              <span className="chip" style={{ cursor: "default" }}>◆ Autopilot</span><span className="chip" style={{ cursor: "default" }}>◇ replay</span>{(night?.recheck?.moments?.length ?? 0) > 0 && <span className="chip" style={{ cursor: "default" }}>↺ re-check</span>}
              <span className="chip" style={{ cursor: "default" }}>▲ too hot · ▼ too cold</span><span className="chip" style={{ cursor: "default" }}>❄ hot · ☼ cold flash · ± your change</span>
            </div>
            {sel && <div className="box" style={{ borderColor: STATUS_COLOR[sel.status] }}><DecisionText d={sel} dd={dd} deg={deg} full /></div>}
            {night && night.decisions.length > 0 && <ShowMore items={night.decisions} limit={8} noun="moments" render={shown => <table className="t"><thead><tr><th>Time</th><th>Stage</th><th>What</th><th>Why</th></tr></thead><tbody>
              {shown.map((d, i) => <tr key={i} onClick={() => setSel(d)} style={{ cursor: "pointer" }}>
                <td className="num">{clock(d.ts, snap.tz)}</td><td>{night.phases.find(p => p.id === d.phase)?.name ?? d.phase}</td>
                <td style={{ color: STATUS_COLOR[d.status] }}>{STATUS_TEXT[d.status]} {d.direction !== "none" ? `${dd(d.delta_c)} ${d.direction}` : ""}</td>
                <td className="mute" style={{ fontSize: 12 }}>{d.reasons.map(r => r.label.toLowerCase()).join(", ")}{d.blocked_by.length ? ` · held back: ${d.blocked_by[0]}` : ""}</td></tr>)}
            </tbody></table>} />}
            {night && night.rows.length > 1 && <div className="row" style={{ justifyContent: "flex-start", gap: 8, flexWrap: "wrap" }}>
              <button className="pill" disabled={rechecking} onClick={recheck}>{rechecking ? "Looking again…" : "Re-check this night"}</button><Tip label="What does re-check do?" text={TIPS.recheck} />
              {night.recheck && <span style={{ fontSize: 13 }} role="status">{recheckText(night.recheck)}
                <span className="faint" style={{ fontSize: 12 }}> · {new Date(night.recheck.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}{night.recheck.used_skin ? ", with skin temperature" : ""}</span></span>}</div>}
            {night?.recheck?.moments && night.recheck.moments.length > 0 && <div style={{ display: "grid", gap: 2 }}>
              {night.recheck.moments.map((m, i) => <span key={i} style={{ fontSize: 12.5 }}><span className="num mute">{clock(m.ts, snap.tz)}</span> · ↺ would have made it {dd(m.delta_c)} {m.direction}{m.why ? ` (${m.why})` : ""}{m.missed ? <b style={{ fontWeight: 500, color: "var(--warm)" }}> · missed live</b> : ""}</span>)}</div>}
            {night && (night.feedback?.note || night.feel.length > 0) && <div style={{ display: "grid", gap: 4 }}>
              <span className="k" style={{ marginTop: 4 }}>What {person.name} said</span>
              {night.feel.map((f, i) => <span key={i} style={{ fontSize: 13 }}><span className="num mute">{clock(f.ts, snap.tz)}</span> · {f.feel.replace(/_/g, " ")}{f.note ? `: "${f.note}"` : ""}</span>)}
              {night.feedback && <span style={{ fontSize: 13 }}>Morning: {night.feedback.temp ? `felt ${night.feedback.temp}` : ""}{night.feedback.rested ? ` · rested ${night.feedback.rested}/5` : ""}{night.feedback.note ? ` · "${night.feedback.note}"` : ""}</span>}
            </div>}
          </div>

          <div className="box">
            <span className="k">What it watches</span>
            <div className="row"><span>Sensitivity<Tip label="What does sensitivity do?" text={TIPS.sensitivity} /></span><div className="seg" style={{ flex: "0 1 240px", minWidth: 0 }}>{(["low", "medium", "high"] as const).map(s => <button key={s} aria-pressed={pol.sensitivity === s} onClick={() => upd({ sensitivity: s })}>{s}</button>)}</div></div>
            <span className="k" style={{ marginTop: 6 }}>Signals it may use</span>
            {meta.signals.map(s => <div className="row" key={s.id}><span style={{ fontSize: 13.5 }}>{s.label}{s.id === "skin_temp" && !st?.has_skin ? <small className="mute"> · no wearable data yet</small> : null}</span>
              <input type="checkbox" className="toggle" checked={pol.signals.includes(s.id)} onChange={e => upd({ signals: e.target.checked ? [...pol.signals, s.id] : pol.signals.filter(x => x !== s.id) })} /></div>)}
            <div style={{ borderTop: "1px solid var(--line)", marginTop: 4 }}>
            <Fold id="studio.ap.trust" className="fold-sub" title="How much each source counts (trust)"
              summary={TRUST.every(([k]) => (pol.trust?.[k] ?? 1) === 1) ? "all normal" : TRUST.filter(([k]) => (pol.trust?.[k] ?? 1) !== 1).map(([k, label]) => `${label.replace("The bed's own sensors", "bed").toLowerCase()} ${trustText(pol.trust?.[k] ?? 1)}`).join(" · ")}>
            <span className="faint" style={{ fontSize: 12 }}>Turn a source down if it's often wrong for you, up if you trust it most.<Tip label="What is trust?" text={TIPS.trust} /></span>
            {TRUST.map(([k, label, help]) => { const v = pol.trust?.[k] ?? 1; return <div key={k} style={{ display: "grid", gap: 2 }}>
              <div className="row"><span style={{ fontSize: 13.5 }}>{label}<Tip label={`What ${label.toLowerCase()} covers`} text={help} /></span><span className="mute num" style={{ fontSize: 12.5 }}>{trustText(v)} · ×{v.toFixed(2).replace(/0$/, "")}</span></div>
              <input type="range" min={0} max={2} step={0.25} value={v} aria-label={`How much ${label.toLowerCase()} counts`} style={{ width: "100%", minHeight: 32 }}
                onChange={e => upd({ trust: { ...(pol.trust ?? { bed: 1, wearable: 1, room: 1 }), [k]: Number(e.target.value) } })} />
              </div>; })}
            </Fold>
            </div>
            <span className="faint" style={{ fontSize: 12 }} role="status">{saving ?? (dirty ? "Saving soon…" : "Changes save automatically.")}</span>
          </div>

          <Fold id="studio.ap.replay" className="box" title="Replay this night with other settings" summary={replay ? `${replay.decisions.length} moment${replay.decisions.length === 1 ? "" : "s"} in the last replay` : "nothing changes"}>
            <span className="mute" style={{ fontSize: 13 }}>Try other settings on {date ?? "this night"} before saving them.<Tip label="How replay works" text={TIPS.replay} /></span>
            <div className="row"><span>Sensitivity</span><div className="seg" style={{ flex: "0 1 260px", minWidth: 0 }}>{["low", "medium", "high"].map(s => <button key={s} aria-pressed={rpSens === s} onClick={() => setRpSens(s)}>{s}</button>)}</div></div>
            <div className="chips">{meta.signals.map(s => { const on = (rpSignals ?? pol.signals).includes(s.id); return <button key={s.id} className="chip" aria-pressed={on} onClick={() => { const cur = rpSignals ?? pol.signals; setRpSignals(on ? cur.filter(x => x !== s.id) : [...cur, s.id]); }}>{s.label}</button>; })}</div>
            <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}><button className="btn pri" onClick={runReplay}>Replay</button>
              {replay && <button className="btn" onClick={() => { upd({ sensitivity: rpSens as AutopilotPolicy["sensitivity"], signals: rpSignals ?? pol.signals }); }}>Use these settings</button>}</div>
            {replay && <span style={{ fontSize: 13 }}>{replay.decisions.length === 0 ? "With these settings it would have done nothing that night." : `It would have: ${Object.entries(replay.summary).map(([k, v]) => `${v} ${STATUS_TEXT[k].toLowerCase()}`).join(", ")}. Shown as ◇ on the chart.`}</span>}
          </Fold>
        </div>

        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div className="box">
            <div className="row"><span className="k">Suggestions</span><span className="mute" style={{ fontSize: 12 }}>{cadenceText}</span></div>
            {lr && !lr.done && <span style={{ fontSize: 12.5, color: "var(--warm)" }}>Still learning ({lr.have} of {lr.need} nights): the phone shows none of these yet.</span>}
            {!sugs || sugs.items.length === 0 ? <span className="mute" style={{ fontSize: 13 }}>Nothing waiting. Suggestions come from changes you made yourself during the night, and from nights where Autopilot and your taps agree.</span>
              : sugs.items.map(s => <div key={s.id} style={{ display: "grid", gap: 4, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
                <b style={{ fontWeight: 500, fontSize: 14 }}>{s.title}</b><span className="mute" style={{ fontSize: 13 }}>{s.detail}</span>
                <div className="row"><span style={{ fontSize: 12.5 }}>{deg(s.proposal.from)} → {deg(s.proposal.to)}</span>
                  <span className="pills"><button className="pill" onClick={async () => { await api.post(`/api/people/${person.id}/autopilot/suggestions/${s.id}/dismiss`, { forever: false }); loadAll(); }}>Not now</button>
                    <button className="btn pri" onClick={() => apply(s.proposal, s.id)}>Apply to rhythm</button></span></div></div>)}
            {sugs?.limits && pol.suggest_cadence !== "off" && <span className="faint" style={{ fontSize: 12 }}>
              The phone shows at most one suggestion each {sugs.limits.one_per}{sugs.limits.used_this_period ? " (already shown this time)" : ""}. "Not now" hides that idea for {sugs.limits.snooze_nights} nights.
              {sugs.limits.snoozed.length > 0 && ` Resting: ${sugs.limits.snoozed.map(x => `${night?.phases.find(p => p.id === x.phase)?.name ?? x.phase.replace(/_/g, " ")} ${x.direction} until ${new Date(x.until * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}`).join(", ")}.`}</span>}
            <span className="k" style={{ marginTop: 6 }}>How often<Tip label="What does how often mean?" text={TIPS.cadence} /></span>
            <div className="seg" style={{ maxWidth: 420 }}>{([["morning", "Every morning"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["off", "Off"]] as const).map(([k, l]) => <button key={k} aria-pressed={pol.suggest_cadence === k} onClick={() => upd({ suggest_cadence: k }, 0)}>{l}</button>)}</div>
            {(pol.suggest_cadence === "weekly" || pol.suggest_cadence === "monthly") && <div className="chips">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d, i) => <button key={d} className="chip" aria-pressed={pol.suggest_weekday === i} onClick={() => upd({ suggest_weekday: i }, 0)}>{d}</button>)}</div>}
            {(() => { const d = nextShow(pol.suggest_cadence, pol.suggest_weekday, snap.now); return d ? <span className="faint" style={{ fontSize: 12 }}>Next ones show on {d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric", timeZone: snap.tz })}.</span> : null; })()}
            <div className="row"><span style={{ fontSize: 13.5 }}>Suggest adding my own night-time changes to the rhythm</span><input type="checkbox" className="toggle" checked={pol.suggest_manual} onChange={e => upd({ suggest_manual: e.target.checked }, 0)} /></div>
          </div>

          <div className="box">
            <span className="k">Trends · last 30 nights</span>
            {trends.map(t => <div key={t.id} style={{ display: "grid", gap: 2 }}><b style={{ fontWeight: 500, fontSize: 13.5 }}>{t.title}</b><span className="mute" style={{ fontSize: 12.5 }}>{t.detail}</span>
              {t.evidence.length > 0 && <div className="chips">{t.evidence.slice(0, 6).map(d => <button key={d} className="chip" onClick={() => setDate(d)}>{d.slice(5)}</button>)}</div>}</div>)}
          </div>

          <div className="box">
            <div className="row"><span className="k">AI review<Tip label="What the AI review reads" text={TIPS.ai} /></span>
              <Tip text={meta.ai ? "An AI model is connected on the server (ET_LLM_URL)." : "No AI model is connected, so the built-in analysis writes the review. Set ET_LLM_URL on the server to use one."}>
                <span className="chip" style={{ cursor: "default", color: meta.ai ? "var(--good)" : "var(--mute)" }}>{meta.ai ? `AI: ${meta.model}` : "built-in (no AI model connected)"}</span></Tip></div>
            <div className="row" style={{ justifyContent: "flex-start", gap: 8 }}><button className="btn pri" disabled={reviewing} onClick={runReview}>{reviewing ? "Thinking…" : `Review ${date ?? "last night"}`}</button>
              <label className="row" style={{ gap: 6, fontSize: 12.5 }}><input type="checkbox" className="toggle" checked={pol.ai_review} onChange={e => upd({ ai_review: e.target.checked })} />Every morning</label></div>
            {review && <div style={{ display: "grid", gap: 6 }}>
              <div style={{ fontSize: 13.5, whiteSpace: "pre-wrap", background: "var(--raise)", borderRadius: 10, padding: "10px 12px" }}>{review.answer}</div>
              {review.proposal && <div className="row"><span style={{ fontSize: 12.5 }}>Suggested: {review.proposal.phase_id} → {deg(review.proposal.to)}</span><button className="btn pri" onClick={() => apply(review.proposal!)}>Apply</button></div>}
            </div>}
          </div>

          <Fold id="studio.ap.limits" className="box" title="Limits"
            summary={`up to ±${u === "F" ? Math.round(pol.max_delta_c * 9 / 5) : pol.max_delta_c}° · at most ${pol.max_actions} changes · ${pol.min_gap_min} min apart`}>
            <span className="faint" style={{ fontSize: 12 }}>The most Autopilot may do in one night.<Tip label="When limits apply" text={TIPS.limits} /></span>
            <Num help={u === "F" ? "The most it may move your temperature in one night, in total. Recommended: 3°." : "The most it may move your temperature in one night, in total. Recommended: 1.5°."} label="Total change per night" v={u === "F" ? Math.round(pol.max_delta_c * 9 / 5) : pol.max_delta_c} min={1} max={u === "F" ? 6 : 3.5} step={u === "F" ? 1 : 0.5} fmt={v => `up to ±${v}°`} set={v => upd({ max_delta_c: u === "F" ? v * 5 / 9 : v })} />
            <Num help="How many separate changes in one night. Recommended: 4." label="Changes per night" v={pol.max_actions} min={1} max={10} step={1} fmt={v => `at most ${v}`} set={v => upd({ max_actions: v })} />
            <Num help="Waits at least this long after a change to see if it helped. Recommended: 20 min." label="Gap between changes" v={pol.min_gap_min} min={10} max={120} step={5} fmt={v => `${v} min`} set={v => upd({ min_gap_min: v })} />
            <Num help="After you press + or − (or a flash), it keeps its hands off for this long. Recommended: 30 min." label="Your own change wins for" v={pol.respect_manual_min} min={0} max={180} step={15} fmt={v => `${v} min`} set={v => upd({ respect_manual_min: v })} />
            <Num help="No changes this close to your wake time, so the warm wake runs as planned. Recommended: 45 min." label="Leave it alone before wake" v={pol.quiet_before_wake_min} min={0} max={120} step={15} fmt={v => `${v} min`} set={v => upd({ quiet_before_wake_min: v })} />
            <Num help="It never sets your side colder than this." label="Never below" v={Math.round(u === "F" ? cToF(pol.floor_c) : pol.floor_c)} min={u === "F" ? 50 : 10} max={Math.min(u === "F" ? 90 : 32, Math.round(u === "F" ? cToF(pol.ceiling_c) : pol.ceiling_c) - 2)} step={1} fmt={v => `${v}°`} set={v => upd({ floor_c: u === "F" ? (v - 32) * 5 / 9 : v })} />
            <Num help="It never sets your side warmer than this." label="Never above" v={Math.round(u === "F" ? cToF(pol.ceiling_c) : pol.ceiling_c)} min={Math.max(u === "F" ? 55 : 13, Math.round(u === "F" ? cToF(pol.floor_c) : pol.floor_c) + 2)} max={u === "F" ? 104 : 40} step={1} fmt={v => `${v}°`} set={v => upd({ ceiling_c: u === "F" ? (v - 32) * 5 / 9 : v })} />
            <span className="k" style={{ marginTop: 6 }}>Parts of the night it may change</span>
            <div className="chips">{(person.phases?.length ? person.phases : night?.phases ?? []).map(ph => <button key={ph.id} className="chip" aria-pressed={pol.phases.includes(ph.id)} onClick={() => upd({ phases: pol.phases.includes(ph.id) ? pol.phases.filter(x => x !== ph.id) : [...pol.phases, ph.id] })}>{ph.name}</button>)}</div>
            <span className="faint" style={{ fontSize: 12 }} role="status">{saving ?? (dirty ? "Saving soon…" : "Changes save automatically.")}</span>
          </Fold>

          <Fold id="studio.ap.wearable" className="box" title="Wearable data"
            summary={`${st?.has_skin ? "skin temperature arriving" : "no skin temperature yet"}${imports.length ? ` · last sync ${new Date(imports[0].ts * 1000).toLocaleDateString([], { month: "short", day: "numeric", timeZone: snap.tz })}` : ""}`}>
            <span className="mute" style={{ fontSize: 13 }}>{st?.has_skin ? "Skin temperature is arriving. It's the strongest signal after your own taps." : "No skin temperature yet. It arrives from the Android app (Health Connect: Pixel Watch, Samsung) or an Oura sync, or a script posting to the import API."}<Tip label="How imports are lined up" text={TIPS.wearable} /></span>
            {imports.length === 0 ? <span className="faint" style={{ fontSize: 12 }}>No imports yet.</span>
              : (() => {
                // group imports sent within 3 minutes of each other into one sync
                const syncs: ImportRow[][] = [];
                for (const im of imports) { const g = syncs[syncs.length - 1]; if (g && Math.abs(g[0].ts - im.ts) < 180) g.push(im); else syncs.push([im]); }
                const today = new Date(snap.now * 1000).toLocaleDateString("en-CA", { timeZone: snap.tz });
                return syncs.slice(0, 4).map((g, i) => { const sm = syncSummary(g, today); return <div key={i} style={{ fontSize: 12.5, borderTop: "1px solid var(--line)", paddingTop: 6, display: "grid", gap: 2 }}>
                  <span><b style={{ fontWeight: 500 }}>Sync · {new Date(g[0].ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: snap.tz })}</b> <span className="faint">from {[...new Set(g.map(x => x.device))].join(", ")}</span></span>
                  <span className="mute">{sm.what}</span>
                  {sm.notes.map((n, k) => <span key={k} className="faint">{n}</span>)}
                </div>; });
              })()}
          </Fold>
        </div>
      </div>
    </>
  );
}

function Num({ label, help, v, min, max, step, fmt, set }: { label: string; help?: string; v: number; min: number; max: number; step: number; fmt: (v: number) => string; set: (v: number) => void }) {
  const c = (x: number) => Math.min(max, Math.max(min, Math.round(x / step) * step));
  return <div className="row ap-num"><span>{label}{help && <small className="faint" style={{ display: "block", fontSize: 11.5 }}>{help}</small>}</span><span className="stepper"><button aria-label={`Less: ${label}`} disabled={v <= min} onClick={() => set(c(v - step))}>−</button><span className="v num" aria-live="polite">{fmt(v)}</span><button aria-label={`More: ${label}`} disabled={v >= max} onClick={() => set(c(v + step))}>+</button></span></div>;
}

function ScoreBar({ d }: { d: Decision }) {
  const span = Math.max(2.5, d.threshold * 2.5);
  const x = (v: number) => 50 + (Math.max(-span, Math.min(span, v)) / span) * 50;
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <div style={{ position: "relative", height: 14, borderRadius: 7, background: "linear-gradient(90deg, color-mix(in srgb,var(--warm) 35%,transparent), var(--raise) 40%, var(--raise) 60%, color-mix(in srgb,var(--cool) 35%,transparent))" }}>
        {[-d.threshold, d.threshold].map(t => <span key={t} style={{ position: "absolute", left: `${x(t)}%`, top: -3, bottom: -3, width: 1.5, background: "var(--text)", opacity: .45 }} />)}
        <span style={{ position: "absolute", left: `calc(${x(d.score)}% - 7px)`, top: 0, width: 14, height: 14, borderRadius: 7, background: "var(--text)" }} />
      </div>
      <div className="row" style={{ fontSize: 11.5 }}><span className="mute">looks cold</span><span className="mute">{d.threshold > 0 ? `${Math.min(999, Math.round(Math.abs(d.score) / d.threshold * 100))}% of the way to a change` : `score ${d.score.toFixed(2)}`}<Tip label="What the score means" text={TIPS.score} /></span><span className="mute">looks warm</span></div>
    </div>
  );
}

function SignalTiles({ s, u, hasSkin }: { s: Record<string, number | string | null>; u: string; hasSkin: boolean }) {
  const f = (v: number | string | null | undefined, fn: (n: number) => string) => typeof v === "number" ? fn(v) : "–";
  const dc = (n: number) => `${n > 0 ? "+" : ""}${(u === "F" ? n * 9 / 5 : n).toFixed(1)}°`;
  const tiles: [string, string][] = [
    ["Restless (10 min)", f(s.restless, n => `${Math.round(n * 100)}%`)],
    ["Heart rate vs settled", f(s.hr_rise, n => `${n > 0 ? "+" : ""}${n.toFixed(0)} bpm`)],
    ["Skin temperature", hasSkin ? f(s.skin_delta, dc) : "no watch data"],
    ["Bed vs plan", f(s.pad_over, dc)],
    ["Room", f(s.room_c, n => `${(u === "F" ? cToF(n) : n).toFixed(0)}°${typeof s.humidity === "number" ? ` · ${s.humidity}%` : ""}`)],
    ["Your last tap", typeof s.feel === "string" ? s.feel.replace(/_/g, " ") : "–"],
  ];
  return <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8 }}>
    {tiles.map(([k, v]) => <div key={k} style={{ background: "var(--raise)", borderRadius: 10, padding: "8px 10px" }}><div className="mute" style={{ fontSize: 11 }}>{k}</div><div className="num" style={{ fontSize: 15 }}>{v}</div></div>)}
  </div>;
}

function DecisionText({ d, dd, deg, full }: { d: Decision; dd: (c: number) => string; deg: (c: number | null | undefined, n?: number) => string; full?: boolean }) {
  const head = d.direction === "none" ? (d.reasons.length ? "Noticed something, not enough to act on." : "Nothing to act on. Temperature looks right.")
    : d.status === "applied" ? `Made it ${dd(d.delta_c)} ${d.direction} (${deg(d.target_before)} → ${deg(d.target_after)}).`
    : d.status === "suggested" ? `Would make it ${dd(d.delta_c)} ${d.direction}${d.target_after != null ? ` (${deg(d.target_before)} → ${deg(d.target_after)})` : ""}.`
    : `Wanted to make it ${dd(d.delta_c)} ${d.direction}, but held back.`;
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <b style={{ fontWeight: 500, fontSize: 14, color: STATUS_COLOR[d.status] }}>{full && <span className="mute num" style={{ fontWeight: 400 }}>{new Date(d.ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} · </span>}{head}</b>
      {d.reasons.length > 0 && <div style={{ display: "grid", gap: 3 }}>
        {d.reasons.map((r, i) => <div key={i} className="row" style={{ fontSize: 12.5, gap: 8 }}>
          <span><b style={{ fontWeight: 500 }}>{r.label}</b> <span className="mute">· {r.value} · {r.detail}</span></span>
          <span className="num" style={{ color: r.push > 0 ? "var(--cool)" : "var(--warm)", whiteSpace: "nowrap" }}>{r.push > 0 ? "warm" : "cold"} {Math.abs(r.push).toFixed(2)}</span></div>)}
      </div>}
      {d.blocked_by.length > 0 && <div style={{ fontSize: 12.5, color: "var(--warm)" }}>{d.blocked_by.map((b, i) => <div key={i}>• {b}</div>)}</div>}
    </div>
  );
}

function NightChart({ n, u, tz, replay, onPick }: { n: NightData; u: string; tz: string; replay: Decision[] | null; onPick: (d: Decision) => void }) {
  const moments = n.recheck?.moments ?? [];
  // no decisions, no replay, nothing in hindsight: an empty score lane is just dashed lines, so say so instead
  const active = n.decisions.length > 0 || (replay?.length ?? 0) > 0 || moments.length > 0;
  const W = 760, LANES = [{ id: "temp", h: 150 }, { id: "body", h: 70 }, { id: "move", h: 36 }, { id: "score", h: active ? 60 : 16 }];
  const H = LANES.reduce((a, l) => a + l.h + 14, 18);
  const box = useRef<HTMLDivElement>(null);
  const [hx, setHx] = useState<number | null>(null);
  const rows = n.rows;
  const t0 = rows[0]?.ts ?? n.start, t1 = Math.max(rows[rows.length - 1]?.ts ?? n.end, t0 + 3600);
  const X = (t: number) => 40 + ((t - t0) / (t1 - t0)) * (W - 50);
  const conv = (c: number) => u === "F" ? cToF(c) : c;
  const temps = rows.flatMap(r => [r.target, r.measured]).filter((v): v is number => v != null).map(conv);
  const lo = temps.length ? Math.min(...temps) - 1 : 60, hi = temps.length ? Math.max(...temps) + 1 : 80;
  let y = 14;
  const lane = LANES.map(l => { const top = y; y += l.h + 14; return { ...l, top }; });
  const L = (id: string) => lane.find(l => l.id === id)!;
  const Yt = (v: number) => L("temp").top + L("temp").h - ((conv(v) - lo) / (hi - lo)) * L("temp").h;
  const path = (pts: [number, number][]) => pts.length < 2 ? "" : "M" + pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" L");
  const hrs = rows.filter(r => r.hr).map(r => r.hr!) ; const hlo = hrs.length ? Math.min(...hrs) - 2 : 40, hhi = hrs.length ? Math.max(...hrs) + 2 : 90;
  const Yh = (v: number) => L("body").top + L("body").h - ((v - hlo) / (hhi - hlo)) * L("body").h;
  const skin = n.skin; const slo = skin.length ? Math.min(...skin.map(s => s[1])) - 0.2 : 33, shi = skin.length ? Math.max(...skin.map(s => s[1])) + 0.2 : 36;
  const Ys = (v: number) => L("body").top + L("body").h - ((v - slo) / (shi - slo)) * L("body").h;
  const score = n.series.score ?? []; const smax = Math.max(2, ...score.map(s => Math.abs(s[1])), ...n.decisions.map(d => Math.abs(d.score)));
  const Yc = (v: number) => L("score").top + L("score").h / 2 - (v / smax) * (L("score").h / 2);
  const thr = n.decisions[0]?.threshold ?? 1;
  const moveBins = useMemo(() => {
    const out: { t: number; f: number }[] = [];
    for (let t = t0; t < t1; t += 600) { const w = rows.filter(r => r.ts >= t && r.ts < t + 600 && r.status != null); if (w.length) out.push({ t, f: w.filter(r => MOVING.has(r.status!)).length / w.length }); }
    return out;
  }, [n]);
  const hover = hx == null ? null : t0 + ((hx - 40) / (W - 50)) * (t1 - t0);
  const near = (arr: { ts: number }[], t: number, gap = 600) => arr.reduce<{ ts: number } | null>((b, x) => Math.abs(x.ts - t) < gap && (!b || Math.abs(x.ts - t) < Math.abs(b.ts - t)) ? x : b, null);
  const hr = hover != null ? rows.reduce<typeof rows[0] | null>((b, r) => !b || Math.abs(r.ts - hover) < Math.abs(b.ts - hover) ? r : b, null) : null;
  const hDec = hover != null ? near(n.decisions, hover, 900) as Decision | null : null;
  const boxW = box.current?.clientWidth ?? W;
  const px = hx != null ? (hx / W) * boxW : 0;
  return (
    <div ref={box} style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block", cursor: "crosshair", touchAction: "pan-y" }}
        onPointerMove={e => { const r = box.current!.getBoundingClientRect(); setHx(((e.clientX - r.left) / r.width) * W); }} onPointerLeave={() => setHx(null)}
        onClick={() => hDec && onPick(hDec)}>
        {n.phases.filter(p => p.start >= t0 && p.start <= t1).map((p, i, arr) => <g key={p.id}><line x1={X(p.start)} x2={X(p.start)} y1={8} y2={H - 4} stroke="var(--line2)" strokeDasharray="2 3" />{X(arr[i + 1]?.start ?? t1) - X(p.start) > p.name.length * 5 + 6 && <text x={X(p.start) + 3} y={10} fontSize="9" fill="var(--mute)">{p.name}</text>}</g>)}
        <text x={2} y={L("temp").top + 10} fontSize="9" fill="var(--mute)">°{u}</text>
        <path d={path(rows.filter(r => r.target != null).map(r => [X(r.ts), Yt(r.target!)]))} fill="none" stroke="var(--acc)" strokeWidth="2" />
        <path d={path(rows.filter(r => r.measured != null).map(r => [X(r.ts), Yt(r.measured!)]))} fill="none" stroke="var(--text)" strokeOpacity=".45" strokeWidth="1.2" />
        <text x={2} y={L("body").top + 10} fontSize="9" fill="var(--mute)">HR</text>
        <path d={path(rows.filter(r => r.hr).map(r => [X(r.ts), Yh(r.hr!)]))} fill="none" stroke="var(--warm)" strokeOpacity=".8" strokeWidth="1" />
        {skin.length > 1 && <path d={path(skin.filter(s => s[0] >= t0 && s[0] <= t1).map(s => [X(s[0]), Ys(s[1])]))} fill="none" stroke="#c48bd6" strokeWidth="1.4" />}
        {skin.length > 1 && <text x={W - 70} y={L("body").top + 10} fontSize="9" fill="#c48bd6">skin temp</text>}
        <text x={2} y={L("move").top + 10} fontSize="9" fill="var(--mute)">moving</text>
        {moveBins.map(b => <rect key={b.t} x={X(b.t)} y={L("move").top + L("move").h * (1 - b.f)} width={Math.max(1, X(b.t + 600) - X(b.t) - 1)} height={L("move").h * b.f} fill="var(--text)" opacity=".35" />)}
        {!active && <text x={40} y={L("score").top + 11} fontSize="10.5" fill="var(--mute)">No Autopilot activity this night</text>}
        {active && <>
        <text x={2} y={L("score").top + 10} fontSize="9" fill="var(--mute)">score</text>
        {[thr, -thr].map(t => <line key={t} x1={40} x2={W - 10} y1={Yc(t)} y2={Yc(t)} stroke="var(--line2)" strokeDasharray="3 3" />)}
        <line x1={40} x2={W - 10} y1={Yc(0)} y2={Yc(0)} stroke="var(--line)" />
        <path d={path(score.map(s => [X(s[0]), Yc(s[1])]))} fill="none" stroke="var(--text)" strokeWidth="1.2" />
        {moments.map((m, i) => <g key={"h" + i}><line x1={X(m.ts)} x2={X(m.ts)} y1={L("temp").top} y2={L("score").top + L("score").h} stroke={m.missed ? "var(--warm)" : "var(--acc)"} strokeOpacity=".55" strokeDasharray="4 3" />
          <text x={X(m.ts)} y={Yc(0) + 4} textAnchor="middle" fontSize="12" fill={m.missed ? "var(--warm)" : "var(--acc)"}><title>{`In hindsight: ${m.direction}${m.missed ? " (missed live)" : ""}`}</title>↺</text></g>)}
        </>}
        {n.decisions.map((d, i) => <text key={i} x={X(d.ts)} y={Yc(d.score) + 4} textAnchor="middle" fontSize="12" fill={STATUS_COLOR[d.status]}>◆</text>)}
        {(replay ?? []).map((d, i) => <text key={"r" + i} x={X(d.ts)} y={Yc(d.score) + 4} textAnchor="middle" fontSize="12" fill={STATUS_COLOR[d.status]}>◇</text>)}
        {n.feel.map((f, i) => <text key={"f" + i} x={X(f.ts)} y={L("temp").top + 10} textAnchor="middle" fontSize="11" fill={f.feel === "too_hot" ? "var(--cool)" : f.feel === "too_cold" ? "var(--warm)" : "var(--good)"}>{f.feel === "too_hot" ? "▲" : f.feel === "too_cold" ? "▼" : "●"}</text>)}
        {n.flashes.map((f, i) => <text key={"x" + i} x={X(f.ts)} y={L("temp").top + 24} textAnchor="middle" fontSize="11" fill={f.kind === "hot_flash" ? "var(--cool)" : "var(--warm)"}>{f.kind === "hot_flash" ? "❄" : "☼"}</text>)}
        {n.manual.map((m, i) => <text key={"m" + i} x={X(m.ts)} y={L("temp").top + L("temp").h} textAnchor="middle" fontSize="11" fill="var(--text)">±</text>)}
        {hx != null && <line x1={hx} x2={hx} y1={8} y2={H - 4} stroke="var(--text)" strokeOpacity=".4" pointerEvents="none" />}
      </svg>
      {hover != null && hr && (
        <div className="scrubtip" style={{ left: px + 14 + 220 <= boxW ? px + 14 : Math.max(0, px - 14 - 220), top: 8, width: 220, pointerEvents: "none" }}>
          <div className="row"><b>{clock(hover, tz)}</b><span className="mute">{n.phases.slice().reverse().find(p => p.start <= hover)?.name ?? ""}</span></div>
          <div className="srow"><span>plan <b>{hr.target != null ? conv(hr.target).toFixed(0) + "°" : "off"}</b> · bed <b>{hr.measured != null ? conv(hr.measured).toFixed(1) + "°" : "–"}</b></span></div>
          {hr.hr && <div className="srow"><span>heart rate <b>{hr.hr}</b></span></div>}
          {hDec && <div className="srow" style={{ color: STATUS_COLOR[hDec.status] }}>{STATUS_TEXT[hDec.status]}: {hDec.reasons.map(r => r.label.toLowerCase()).join(", ")} · click for why</div>}
        </div>
      )}
    </div>
  );
}

/** The recheck result in plain words (older saved results have no text yet). */
function recheckText(r: Recheck): string {
  if (r.text) return r.text;
  const n = r.decisions.filter(d => d.status === "suggested" || d.status === "applied").length;
  return n === 0 ? "Looked again with all the data now: nothing it would have changed." : `${n} moment${n === 1 ? "" : "s"} it would have changed.`;
}

/** "Normal for you": the learned comfort band for each reading, as a short sentence plus a small band per reading
 *  with today's value marked. Inside it Autopilot stays quiet. */
function NormalRange({ band, cur, deg, u, quiet }: { band: Band; cur?: Status["current"]; deg: (c: number | null | undefined, d?: number) => string; u: string; quiet: boolean }) {
  const rows: { key: string; label: string; lo: number; hi: number; now: number | null | undefined; fmt: (v: number) => string; pad: number }[] = [];
  if (band.bed_c) rows.push({ key: "bed", label: "Bed", lo: band.bed_c[0], hi: band.bed_c[1], now: cur?.bed_c, fmt: v => deg(v), pad: u === "F" ? 0.6 : 0.6 });
  if (band.hr) rows.push({ key: "hr", label: "Heart rate", lo: band.hr[0], hi: band.hr[1], now: cur?.hr, fmt: v => `${Math.round(v)}`, pad: 4 });
  if (band.skin) rows.push({ key: "skin", label: "Skin", lo: band.skin[0], hi: band.skin[1], now: cur?.skin, fmt: v => deg(v, 1), pad: 0.4 });
  if (!rows.length) return null;
  const words = rows.map(r => `${r.label.toLowerCase()} ${r.fmt(r.lo)}–${r.fmt(r.hi)}`).join(", ");
  return (
    <div style={{ display: "grid", gap: 6, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
      <span style={{ fontSize: 13.5 }}>Normal for you: {words}{band.restless != null ? `, restless up to ${Math.round(band.restless * 100)}% of the time` : ""}. Autopilot stays quiet inside this.<Tip label="What is the normal range?" text={TIPS.band} /></span>
      {rows.map(r => {
        const span = r.hi - r.lo || 1, a = r.lo - span - r.pad, b = r.hi + span + r.pad;
        const x = (v: number) => `${Math.max(0, Math.min(100, ((v - a) / (b - a)) * 100))}%`;
        const inside = r.now != null && r.now >= r.lo && r.now <= r.hi;
        return <div key={r.key} style={{ display: "grid", gridTemplateColumns: "84px 1fr", gap: 8, alignItems: "center" }}>
          <span className="mute" style={{ fontSize: 12 }}>{r.label}</span>
          <div style={{ position: "relative", height: 14, borderRadius: 7, background: "var(--raise)" }} aria-label={`${r.label}: normal ${r.fmt(r.lo)} to ${r.fmt(r.hi)}${r.now != null ? `, now ${r.fmt(r.now)}` : ""}`} role="img">
            <span style={{ position: "absolute", left: x(r.lo), width: `calc(${x(r.hi)} - ${x(r.lo)})`, top: 0, bottom: 0, borderRadius: 7, background: "var(--good)", opacity: .35 }} />
            {r.now != null && <span title={`now ${r.fmt(r.now)}`} style={{ position: "absolute", left: `calc(${x(r.now)} - 2px)`, top: -2, width: 4, height: 18, borderRadius: 2, background: inside ? "var(--text)" : "var(--warm)" }} />}
          </div></div>;
      })}
      <span className="faint" style={{ fontSize: 12 }}>{quiet ? "Right now everything is inside your normal range." : "The marks show the latest readings while you're in bed."}</span>
    </div>
  );
}
