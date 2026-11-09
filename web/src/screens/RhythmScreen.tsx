import { rhythmNights } from "../components/WorkDays";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import Tip from "../components/Tip";
import { useApp } from "../ctx";
import { stepC, tempNum } from "../format";
import type { Phase, Rhythm, Schedule } from "../types";


function shiftHM(hm: string, min: number): string {
  const [h, m] = hm.split(":").map(Number);
  const t = ((h * 60 + m + min) % 1440 + 1440) % 1440;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}
function fmtHM(hm: string): string {
  const [h, m] = hm.split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}
function startText(p: Phase): string {
  const a = p.start, off = Math.abs(a.offset_min);
  const rel = (what: string) => (a.offset_min === 0 ? `at ${what}` : `${off} min ${a.offset_min < 0 ? "before" : "after"} ${what}`);
  if (a.kind === "clock") return `at ${fmtHM(a.time ?? "03:00")}`;
  if (a.kind === "bedtime") return rel("bedtime");
  if (a.kind === "in_bed") return a.offset_min === 0 ? "when you get in bed" : rel("getting in bed");
  return rel("wake");
}

function TimeStep({ label, value, onChange, tip }: { label: string; value: string; onChange: (v: string) => void; tip?: string }) {
  return (
    <div className="row">
      <span>{label}{tip && <Tip text={tip} label={`About ${label.toLowerCase()}`} />}</span>
      <span className="stepper">
        <button aria-label={`${label} 15 minutes earlier`} onClick={() => onChange(shiftHM(value, -15))}>−</button>
        <span className="v num">{fmtHM(value)}</span>
        <button aria-label={`${label} 15 minutes later`} onClick={() => onChange(shiftHM(value, 15))}>+</button>
      </span>
    </div>
  );
}

export default function RhythmScreen() {
  const { me, snap, say } = useApp();
  const [rhythms, setRhythms] = useState<Rhythm[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const timer = useRef<number>();
  useEffect(() => {
    if (me) api.get<Rhythm[]>(`/api/people/${me.id}/rhythms`).then(r => { setRhythms(r); setSel(s => s ?? me.schedule?.rhythm_id ?? r[0]?.id); });
  }, [me?.id]);
  if (!me || !rhythms || !snap) return null;
  const u = me.settings.units;
  const r = rhythms.find(x => x.id === sel) ?? rhythms[0];
  const tonight = me.schedule?.rhythm_id === r.id;

  // edits apply locally at once and save after a short pause, so rapid taps don't spam the server
  const save = (next: Rhythm) => {
    setRhythms(rs => rs!.map(x => (x.id === next.id ? next : x)));
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api.put(`/api/people/${me.id}/rhythms/${next.id}`, next).then(() => say("Saved")).catch(e => say(`Couldn't save: ${e.message}`));
    }, 700);
  };
  const setPhase = (i: number, patch: Partial<Phase>) => { const n = structuredClone(r); n.phases[i] = { ...n.phases[i], ...patch }; save(n); };
  const setSched = (patch: Partial<Schedule>) => save({ ...r, schedule: { ...r.schedule, ...patch } });

  return (
    <div className="screen">
      <div><div className="k">Rhythm</div><h1 style={{ fontSize: 28 }}>{r.name}</h1>
        <div className="mute" style={{ fontSize: 13 }}>{rhythmNights(r)}{tonight ? " · tonight" : ""}</div></div>
      {rhythms.length > 1 && <div className="seg">{rhythms.map(x => <button key={x.id} aria-pressed={x.id === r.id} onClick={() => setSel(x.id)}>{x.name}</button>)}</div>}

      <div className="tcols">
      <section className="tcol">
      <div className="k">Bedtime and wake</div>
      <div className="card list">
        <TimeStep label="Usually in bed" value={r.schedule.bedtime} onChange={v => setSched({ bedtime: v })} />
        <TimeStep label="Wake" value={r.schedule.wake} onChange={v => setSched({ wake: v })} />
        <div className="row"><span>Start when I'm in bed on my side<br /><span className="mute" style={{ fontSize: 12.5 }}>Otherwise it starts at your usual bedtime</span></span>
          <input type="checkbox" className="toggle" checked={r.schedule.start_on_presence} onChange={e => setSched({ start_on_presence: e.target.checked })} /></div>
        <div style={{ display: "grid", gap: 8, padding: "11px 0", borderBottom: "1px solid var(--line)" }}><span>Pre-cool before bedtime<Tip label="About pre-cool" text="Your side gets to its first temperature before bedtime, so the bed is ready when you get in. If your phone shows you're out, it waits." /></span>
          <span className="chips">{[30, 45, 60].map(m => <button key={m} className="chip" aria-pressed={r.schedule.precool_lead_min === m} onClick={() => setSched({ precool_lead_min: m })}>{m} min</button>)}</span></div>
        {r.schedule.start_on_presence && <>
          <TimeStep label="Earliest start" value={r.schedule.window_start} onChange={v => setSched({ window_start: v })}
            tip="Getting in bed before this doesn't start the night, so an evening nap or reading in bed won't." />
          <TimeStep label="Keep pre-cooling until" value={r.schedule.window_end} onChange={v => setSched({ window_end: v })}
            tip="If your side is still empty after this, it stops pre-cooling to save power. Getting in later still starts your night." />
        </>}
      </div>

      </section>
      <section className="tcol">
      <div className="k">Phases · tap one to edit</div>
      <div className="card list">
        {r.phases.map((p, i) => {
          const isOpen = open === p.id;
          return (
            <div className="item" key={p.id} data-nav=""
              onKeyDown={e => {        // keyboard phones: ← → change this phase's temperature (J/K or ↑↓ move between phases)
                if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                const t = e.target as HTMLElement;
                if (t.tagName === "INPUT" || t.tagName === "SELECT") return;
                e.preventDefault();
                setPhase(i, { temp_c: Math.round((p.temp_c + (e.key === "ArrowRight" ? 1 : -1) * stepC(u)) * 100) / 100 });
              }}>
              <button onClick={() => setOpen(isOpen ? null : p.id)} aria-expanded={isOpen}
                style={{ all: "unset", cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%", gap: 10 }}>
                <span>{p.name}{me.night.phase_id === p.id && tonight ? <span className="mute" style={{ fontSize: 12 }}> · now</span> : null}<br />
                  <span className="mute" style={{ fontSize: 12.5 }}>{startText(p)}</span></span>
                <span className="num" style={{ fontSize: 22, fontWeight: 300, color: p.id === "wake" ? "var(--warm)" : "var(--text)" }}>{tempNum(p.temp_c, u)}°</span>
              </button>
              {isOpen && (
                <div style={{ display: "grid", gap: 12, paddingTop: 12 }}>
                  <div className="row"><span className="mute">Temperature</span>
                    <span className="stepper"><button aria-label="Cooler" onClick={() => setPhase(i, { temp_c: Math.round((p.temp_c - stepC(u)) * 100) / 100 })}>−</button>
                      <span className="v num">{tempNum(p.temp_c, u)}°</span>
                      <button aria-label="Warmer" onClick={() => setPhase(i, { temp_c: Math.round((p.temp_c + stepC(u)) * 100) / 100 })}>+</button></span></div>
                  {p.start.kind === "clock" ? (
                    <TimeStep label="Starts at" value={p.start.time ?? "03:00"} onChange={v => setPhase(i, { start: { ...p.start, time: v } })} />
                  ) : (
                    <div className="row"><span className="mute">Starts</span>
                      <span className="stepper"><button aria-label="15 minutes earlier" onClick={() => setPhase(i, { start: { ...p.start, offset_min: p.start.offset_min - 15 } })}>−</button>
                        <span className="v num" style={{ minWidth: 128, fontSize: 13 }}>{(() => { const m = Math.abs(p.start.offset_min); const what = p.start.kind === "in_bed" ? "in bed" : p.start.kind === "wake" ? "wake" : "bedtime";
                          return p.start.offset_min === 0 ? `at ${what}` : `${m} min ${p.start.offset_min > 0 ? "after" : "before"} ${what}`; })()}</span>
                        <button aria-label="15 minutes later" onClick={() => setPhase(i, { start: { ...p.start, offset_min: p.start.offset_min + 15 } })}>+</button></span></div>
                  )}
                  <div style={{ display: "grid", gap: 8 }}><span className="mute">Change over gradually<Tip label="About changing over gradually" text="How long the bed takes to glide from the previous temperature to this one. Now = straight away." /></span>
                    <span className="chips">{[0, 10, 20, 30].map(m => <button key={m} className="chip" aria-pressed={p.ramp_min === m} onClick={() => setPhase(i, { ramp_min: m })}>{m ? `${m} min` : "now"}</button>)}</span></div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <p className="mute" style={{ fontSize: 12.5, textAlign: "center" }}>Change what a phase is anchored to, or edit against past nights, in <Link to="/studio/rhythm">Studio</Link>.</p>
      </section>
      </div>
    </div>
  );
}
