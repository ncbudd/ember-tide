import { rhythmNights } from "../components/WorkDays";
import Icon from "../components/Icon";
import { GuestCard } from "../components/GuestCard";
import { useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, isApp } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../ctx";
import { clock, colorKey, COLORS, fToC, mmss, personaColor, stepC, tempNum } from "../format";
import type { PersonSettings, PersonView, Rhythm, Snapshot } from "../types";
import AwayNote from "../components/AwayNote";
import PhoneNotifications from "./PhoneNotifications";
import PhoneSources from "./PhoneSources";

/**
 * The Simple app: for someone who just wants their side of the bed to be right. Three tabs (Home, Schedule, Me),
 * big buttons, plain words, no charts. Everything else stays in Studio for whoever set it up.
 */

/** What's happening, in a few friendly words, plus the big number (or word) to show. */
function describe(p: PersonView, snap: Snapshot): { big: string; line: string; on: boolean } {
  const n = p.night, u = p.settings.units, tz = snap.tz;
  const f = n.flash && snap.now < n.flash.end ? n.flash : null;
  if (f) return { big: tempNum(f.target_c, u), line: `${f.kind === "hot" ? "Cooling you off" : "Warming you up"} · ${mmss(f.end - snap.now)} left`, on: true };
  if (n.target_c != null) {
    const m = p.bed.measured_c;
    const t = n.target_c;
    let line = "Holding your temperature";
    if (n.override?.manual) line = n.override.end_ts ? `On until ${clock(n.override.end_ts, tz)}` : "On until bedtime";
    else if (n.state === "waiting") line = m != null && t < m - 0.4 ? "Cooling down for bed" : m != null && t > m + 0.4 ? "Warming up for bed" : "Ready for bed";
    else if (n.state === "out_of_bed_hold") line = "Keeping your side ready while you're up";
    else if (n.phase_id === "wake") line = "Warming up gently for the morning";
    else if (m != null && t < m - 0.4) line = `Cooling to ${tempNum(t, u)}°`;
    else if (m != null && t > m + 0.4) line = `Warming to ${tempNum(t, u)}°`;
    if (n.flash && snap.now >= n.flash.end) line = "Easing back to your temperature";
    if (n.override && !n.override.manual && n.override.delta_c) line += ` · ${n.override.delta_c < 0 ? "cooler" : "warmer"} than usual`;
    return { big: tempNum(t, u), line, on: true };
  }
  const sc = p.schedule;
  const starts = sc ? `Starts at ${clock(sc.precool, tz)}` : "No schedule yet";
  switch (n.state) {
    case "skipped": return { big: "Off", line: "Off for tonight", on: false };
    case "paused": return { big: "Off", line: n.pause_until ? `Paused until ${clock(n.pause_until, tz)}` : "Paused", on: false };
    case "away": return { big: "Away", line: n.away ? "See you when you're back" : "It will wait until you're home", on: false };
    case "done": return { big: "Off", line: "All done · see you tonight", on: false };
    case "missed": return { big: "Off", line: "It starts when you get into bed", on: false };
    case "no_rhythm": return { big: "Off", line: "No schedule yet", on: false };
    default: return { big: "Off", line: starts, on: false };
  }
}

function Home() {
  const { snap, me, act, say } = useApp();
  const { isOwner } = useAuth();
  const [lastNight, setLastNight] = useState<{ date: string; temp?: string } | null>(null);
  const [thanks, setThanks] = useState(false);
  const morning = snap ? (() => { const h = Number(new Intl.DateTimeFormat("en-US", { hour: "numeric", hour12: false, timeZone: snap.tz }).format(new Date(snap.now * 1000))); return h >= 5 && h < 13; })() : false;
  useEffect(() => {
    if (!me || !morning) return;
    api.get<{ date: string; in_progress: boolean; has_data: boolean; feedback: { temp?: string } | null }[]>(`/api/people/${me.id}/nights?count=2`)
      // only the newest night, once it's over: never ask about the night before by mistake
      .then(ns => { const n = ns[0]; setLastNight(n && !n.in_progress && n.has_data ? { date: n.date, temp: n.feedback?.temp } : null); })
      .catch(() => {});
  }, [me?.id, morning, snap ? Math.floor(snap.now / 600) : 0]);
  if (!snap || !me) return null;
  if (me.night.guest) return <div className="sa-home"><GuestCard person={me} /></div>;   // your side is lent to a guest
  const n = me.night, u = me.settings.units;
  const base = `/api/people/${me.id}`;
  const f = n.flash && snap.now < n.flash.end ? n.flash : null;
  const d = describe(me, snap);
  const step = stepC(u);
  const resumable = n.state === "paused" || n.state === "skipped";
  const idle = n.target_c == null && !resumable && !f;
  const first = me.phases[0]?.temp_c ?? fToC(70);
  const adjust = (sign: number) => idle
    ? act(`${base}/adjust`, { temp_c: (me.bed.setpoint_c ?? first) + sign * step, until: "next_phase" })
    : act(`${base}/adjust`, { delta_c: sign * step, until: "next_phase" });
  const rate = async (temp: string) => {
    if (!lastNight) return;
    try { await api.post(`${base}/feedback`, { date: lastNight.date, temp }); setLastNight({ ...lastNight, temp }); setThanks(true); }
    catch { say("Couldn't save that"); }
  };
  return (
    <div className="screen sa sa-home">
      <div className="sa-top"><span style={{ color: personaColor(me), fontWeight: 500 }}>{me.name}</span><span className="mute num">{clock(snap.now, snap.tz)}</span></div>

      <div className="sa-hero" aria-live="polite">
        <span className={`big sa-temp${d.on ? "" : " off"}`} style={{ color: f ? (f.kind === "hot" ? "var(--cool)" : "var(--warm)") : d.on ? "var(--text)" : "var(--mute)" }}>
          {d.big}{d.on && <span className="deg">°</span>}</span>
        <span className="sa-line">{d.line}</span>
      </div>

      <AwayNote person={me} me={me} isOwner={isOwner} tz={snap.tz} />

      {morning && lastNight && !lastNight.temp && !thanks && (
        <div className="card sa-check">
          <span>How did you sleep?</span>
          <div className="sa-three">
            {[["cold", "Too cold"], ["right", "Just right"], ["warm", "Too warm"]].map(([k, l]) => <button key={k} onClick={() => rate(k)}>{l}</button>)}
          </div>
        </div>
      )}
      {thanks && <p className="mute" style={{ textAlign: "center" }}>Thanks. That helps get your nights right.</p>}

      <div className="sa-controls">
        <div className="sa-flashes">
          {f ? <>
            <button onClick={() => act(`${base}/flash/extend`)}>{me.settings[f.kind === "hot" ? "hot_flash" : "cold_flash"].extend_min} more minutes</button>
            <button onClick={() => act(`${base}/flash/stop`)}>That's enough</button>
          </> : <>
            <button className="hot" onClick={() => act(`${base}/flash`, { kind: "hot" })}><Icon name="snow" className="fl-ico" /> Hot flash</button>
            <button className="cold" onClick={() => act(`${base}/flash`, { kind: "cold" })}><Icon name="sun" className="fl-ico" /> Cold flash</button>
          </>}
        </div>
        <div className="sa-steps">
          <button aria-label="Warmer" disabled={!!f} onClick={() => adjust(1)}>+<small>Warmer</small></button>
          <button aria-label="Cooler" disabled={!!f} onClick={() => adjust(-1)}>−<small>Cooler</small></button>
        </div>
      </div>

      <div className="sa-power">
        {n.override && !n.override.manual && !f && <button className="btn" onClick={() => act(`${base}/adjust/clear`)}>Back to my usual</button>}
        {!n.away && <button className="btn" onClick={() => { void act(`${base}/away`, { away: true }); say("You're marked away. Your side stays off until you're back."); }}>Away for a few days</button>}
        {resumable ? <button className="btn" onClick={() => act(`${base}/tonight`, { action: "resume" })}>Turn back on</button>
          : n.override?.manual ? <button className="btn" onClick={() => act(`${base}/adjust/clear`)}>Turn off</button>
          : idle ? <button className="btn" onClick={() => act(`${base}/adjust`, { temp_c: first, until: "next_phase" })}>Turn on now</button>
          : <button className="btn" onClick={() => act(`${base}/tonight`, { action: "skip" })}>Off tonight</button>}
      </div>
    </div>
  );
}

// ---------- Schedule ----------
const eve = (hm: string) => { const [h, m] = hm.split(":").map(Number); return ((h < 12 ? h + 24 : h) - 12) * 60 + m; };   // minutes after noon
const fromEve = (x: number) => { const t = ((Math.round(x) % 1440) + 1440 + 720) % 1440; return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };

/** Bedtime moved: keep "earliest start" an hour before it and "keep pre-conditioning until" a couple of hours after. */
function withBedtime(rh: Rhythm, bed: string): Rhythm {
  const s = { ...rh.schedule, bedtime: bed };
  const b = eve(bed);
  if (eve(s.window_start) > b - 60) s.window_start = fromEve(Math.max(0, b - 60));
  if (eve(s.window_end) < b + 120) s.window_end = fromEve(Math.min(eve(s.wake) - 60, b + 195));
  return { ...rh, schedule: s };
}

function ScheduleCard({ rh, units, onSave }: { rh: Rhythm; units: "F" | "C"; onSave: (r: Rhythm) => Promise<boolean> }) {
  const [cur, setCurState] = useState(rh);
  const latest = useRef(rh);                                  // quick taps build on each other, not on a stale render
  const setCur = (r: Rhythm) => { latest.current = r; setCurState(r); };
  const [state, setState] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => { if (!timer.current) setCur(rh); }, [rh]);
  const save = (next: Rhythm, delay = 700) => {
    setCur(next);
    setState("Saving…");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => { timer.current = undefined; setState((await onSave(latest.current)) ? "Saved" : null); }, delay);
  };
  // an edit still waiting for its timer is saved at once when you leave the tab or the page is hidden, not lost
  const flush = () => {
    if (!timer.current) return;
    window.clearTimeout(timer.current);
    timer.current = undefined;
    void onSave(latest.current);
  };
  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(() => {
    const hide = () => { if (document.visibilityState === "hidden") flushRef.current(); };
    document.addEventListener("visibilitychange", hide);
    return () => { document.removeEventListener("visibilitychange", hide); flushRef.current(); };
  }, []);
  const step = units === "F" ? 5 / 9 : 0.5;
  const coolest = Math.min(...cur.phases.map(p => p.temp_c));
  const shift = (sign: number) => { const c = latest.current; save({ ...c, phases: c.phases.map(p => ({ ...p, temp_c: Math.round((p.temp_c + sign * step) * 100) / 100 })) }, 900); };
  return (
    <div className="card sa-sched">
      <div className="row"><b style={{ fontWeight: 500 }}>{cur.name}</b><span className="mute" style={{ fontSize: 13 }}>{rhythmNights(cur)}</span></div>
      <label className="sa-time"><span>Bedtime</span>
        <input type="time" value={cur.schedule.bedtime} onChange={e => e.target.value && save(withBedtime(latest.current, e.target.value))} aria-label={`Bedtime, ${cur.name}`} />
      </label>
      <label className="sa-time"><span>Wake up</span>
        <input type="time" value={cur.schedule.wake} onChange={e => e.target.value && save({ ...latest.current, schedule: { ...latest.current.schedule, wake: e.target.value } })} aria-label={`Wake up, ${cur.name}`} />
      </label>
      <div className="sa-overall">
        <span>Overall temperature<br /><span className="mute" style={{ fontSize: 12.5 }}>Coolest part of the night {tempNum(coolest, units)}°</span></span>
        <div className="sa-pair">
          <button onClick={() => shift(-1)} aria-label={`Cooler, ${cur.name}`}>− Cooler</button>
          <button onClick={() => shift(1)} aria-label={`Warmer, ${cur.name}`}>+ Warmer</button>
        </div>
      </div>
      <span className="mute" role="status" aria-live="polite" style={{ fontSize: 12.5, minHeight: 18 }}>{state ?? ""}</span>
    </div>
  );
}

function Schedule() {
  const { me, say } = useApp();
  const [rhythms, setRhythms] = useState<Rhythm[] | null>(null);
  useEffect(() => { if (me) api.get<Rhythm[]>(`/api/people/${me.id}/rhythms`).then(setRhythms).catch(() => setRhythms([])); }, [me?.id]);
  if (!me) return null;
  const onSave = async (r: Rhythm) => {
    try { await api.put(`/api/people/${me.id}/rhythms/${r.id}`, r); setRhythms(rs => rs?.map(x => x.id === r.id ? r : x) ?? null); return true; }
    catch (e) { say(`Couldn't save: ${(e as Error).message}`); return false; }
  };
  return (
    <div className="screen sa">
      <h2 style={{ fontSize: 28 }}>Your schedule</h2>
      <p className="mute">Your side cools down before bed and warms up gently before you wake. It starts when you get into bed.</p>
      {rhythms === null ? <p className="mute">Loading…</p>
        : rhythms.length === 0 ? <p className="mute">No schedule yet. Ask the person who set up Ember &amp; Tide to add one in Studio.</p>
        : <div className="sa-sched-list">{rhythms.map(r => <ScheduleCard key={r.id} rh={r} units={me.settings.units} onSave={onSave} />)}</div>}
      <p className="faint" style={{ fontSize: 12.5 }}>The exact temperatures through the night can be fine-tuned in Studio.</p>
    </div>
  );
}

// ---------- Me ----------
function Me() {
  const { me, snap, put, say } = useApp();
  const auth = useAuth();
  const [name, setName] = useState(me?.name ?? "");
  useEffect(() => setName(me?.name ?? ""), [me?.id]);
  if (!me || !snap) return null;
  const s = me.settings;
  const set = (patch: Partial<PersonSettings>) => put(`/api/people/${me.id}/settings`, { ...s, ...patch });
  const saveName = async () => {
    const n = name.trim();
    if (!n || n === me.name) return;
    try { await api.put(`/api/people/${me.id}/profile`, { name: n }); say("Saved"); } catch (e) { say(`Couldn't save: ${(e as Error).message}`); }
  };
  return (
    <div className="screen sa">
      <h2 style={{ fontSize: 28 }}>Me</h2>
      <div className="card list">
        <label className="row"><span>Name</span>
          <input type="text" value={name} maxLength={40} style={{ width: 170, fontSize: 16, padding: "10px 12px" }} onChange={e => setName(e.target.value)} onBlur={saveName} /></label>
        <div className="item" style={{ display: "grid", gap: 10 }}>
          <span>Your colour</span>
          <div className="swatches" role="radiogroup" aria-label="Your colour">
            {COLORS.map(c => { const on = colorKey(me) === c.key;
              return <button key={c.key} role="radio" aria-checked={on} aria-label={c.name} title={c.name} style={{ background: `var(--pc-${c.key})` }} onClick={() => set({ color: c.key })}>{on ? "✓" : ""}</button>; })}
          </div>
        </div>
        <div className="item" style={{ display: "grid", gap: 8 }}>
          <span>Look</span>
          <div className="seg sa-seg">{(["system", "light", "dark"] as const).map(k =>
            <button key={k} aria-pressed={s.appearance === k || (k === "system" && s.appearance === "schedule")} onClick={() => set({ appearance: k })}>{k === "system" ? "Like my phone" : k === "light" ? "Light" : "Dark"}</button>)}</div>
        </div>
        <div className="row"><span>Temperatures in</span>
          <div className="seg sa-seg" style={{ width: 140 }}>{(["F", "C"] as const).map(k => <button key={k} aria-pressed={s.units === k} onClick={() => set({ units: k })}>°{k}</button>)}</div></div>
      </div>
      {isApp() && <PhoneNotifications />}
      {isApp() && <PhoneSources compact />}
      <div className="card" style={{ gap: 12 }}>
        <span>More options</span>
        <span className="mute" style={{ fontSize: 13.5 }}>Charts, sleep details and fine-tuning are in Studio, set up by the person who installed Ember &amp; Tide. Ask them if something needs changing.</span>
        <button className="btn" onClick={() => set({ app_mode: "full" })}>Switch to the full app</button>
        {auth.state.enabled && <button className="link" style={{ justifySelf: "start" }} onClick={() => void auth.signOut()}>Sign out</button>}
      </div>
    </div>
  );
}

export default function SimpleApp() {
  return <>
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/schedule" element={<Schedule />} />
      <Route path="/me" element={<Me />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    <nav className="tabs sa-tabs" aria-label="Main">
      <NavLink end to="/" data-key="1">Home</NavLink><NavLink to="/schedule" data-key="2">Schedule</NavLink><NavLink to="/me" data-key="3">Me</NavLink>
    </nav>
  </>;
}
