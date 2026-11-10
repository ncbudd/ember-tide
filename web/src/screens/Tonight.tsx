import Icon from "../components/Icon";
import TodayCard from "../components/TodayCard";
import { GuestCard, GuestStart } from "../components/GuestCard";
import { useAuth } from "../auth";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { allow, fitGuard } from "../components/fitGuard";
import { api } from "../api";
import Horizon from "../components/Horizon";
import Sheet from "../components/Sheet";
import Glance from "../components/Glance";
import { useLayout } from "../components/layout";
import { useApp } from "../ctx";
import { clock, contextText, deltaText, isNight, mins, mmss, personaColor, STATE_TEXT, stepC, tempNum } from "../format";
import type { PersonView, View } from "../types";
import Simple from "./Simple";
import AwayNote from "../components/AwayNote";
import { CareTonight } from "../components/Care";
import PetQuestion from "../components/PetQuestion";
import { useHardwareKeyboard } from "../keyboard";

function BigTemp({ c, units, color, size, stale }: { c: number | null; units: "F" | "C"; color?: string; size?: number; stale?: boolean }) {
  // "Off" is a word, not a number: at the numeral's size its round O looks huge, so it sits smaller on the same line
  if (c == null) return <span className={`big off${stale ? " stale" : ""}`} style={{ fontSize: size ? size * 0.6 : undefined, color: "var(--mute)" }}>Off</span>;
  return <span className={`big${stale ? " stale" : ""}`} style={{ fontSize: size, color }}>{tempNum(c, units)}<span className="deg">°</span></span>;
}

const LENGTHS: [number | null, string, string][] = [[30, "30m", "30 minutes"], [60, "1h", "1 hour"], [90, "1½h", "1½ hours"], [120, "2h", "2 hours"], [null, "Bedtime", "until bedtime"]];

/**
 * The rhythm has the side off (daytime, or done for the night): turn it on by hand, e.g. for a nap.
 * The temperature is set with the dock's Cooler/Warmer below (one set of controls); Turn on sits on the right, under the thumb.
 */
function TurnOn({ t, units, len, setLen, endsAt, onStart, connected }: {
  connected: boolean; t: number; units: "F" | "C"; len: number | null; setLen: (m: number | null) => void; endsAt: string; onStart: () => void;
}) {
  return <div className="card turnon">
    <div className="seg lens" role="group" aria-label="For how long">
      {LENGTHS.map(([m, l, full]) => <button key={l} aria-pressed={len === m} aria-label={full} onClick={() => setLen(m)}>{l}</button>)}
    </div>
    <div className="row">
      <span><span className="num" style={{ fontSize: 22 }}>{tempNum(t, units)}°</span> <span className="mute" style={{ fontSize: 13 }}>{endsAt}</span>
        <small className="mute" style={{ display: "block", fontSize: 12 }}>Set it with Cooler and Warmer</small></span>
      <button className="btn pri go" data-key="n" disabled={!connected} onClick={onStart}>Turn on</button>
    </div>
  </div>;
}

export default function Tonight({ setSimpleOn }: { setSimpleOn: (v: boolean) => void }) {
  const { snap, me, partner, act, say, connected } = useApp();
  // a keyboard on a large/XL phone (compact mode with room to spare): the daytime chart may grow into the height
  const roomy = useHardwareKeyboard() && window.innerHeight >= 700;
  const [view, setView] = useState<View | null>(null);
  // Simple is "sticky": once it opens (by default or because you got in bed) it stays until you leave it,
  // so turning your side off or a flash ending never changes the screen under you.
  const [simple, setSimple] = useState<boolean | null>(() => (me?.settings.simple_default ? true : null));
  const [acting, setActing] = useState<string | null>(null);
  const [until, setUntil] = useState<"next_phase" | "night">("next_phase");
  const [sheet, setSheet] = useState(false);
  const [guestSheet, setGuestSheet] = useState(false);
  const { isOwner } = useAuth();
  const [onTemp, setOnTemp] = useState<number | null>(null);      // what "Turn on" will use; null = the fall-asleep temperature
  const [onLen, setOnLen] = useState<number | null>(60);
  // The plan list always shows at least two rows. On a short screen the chart and the big temperature give up height
  // to make that true (the chart most, then the number); on a tall one the list simply gets the extra room.
  const planRef = useRef<HTMLDivElement>(null);
  // the chart is drawn at its real width, so its text and line weights stay the same size on any screen
  const chartRef = useRef<HTMLDivElement>(null);
  const [chartW, setChartW] = useState(340);
  useLayoutEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const f = () => { const w = Math.round(el.clientWidth); if (w > 0) setChartW(c => (Math.abs(c - w) > 4 && allow(guards.current.chart) ? Math.max(300, w) : c)); };
    f();
    const ro = new ResizeObserver(f);
    ro.observe(el);
    return () => ro.disconnect();
  });
  const guards = useRef({ squeeze: fitGuard(), day: fitGuard(), fold: fitGuard(), chart: fitGuard() });   // see fitGuard.ts
  const [squeeze, setSqueeze] = useState(0);                 // px taken back from the chart + number
  const [planMin, setPlanMin] = useState(0);                 // two rows' height: the list never gets smaller than this
  // squeeze > 0: give height back (small screens); squeeze < 0: grow into spare room once every plan row already shows
  const chartH = Math.round(squeeze >= 0 ? 130 - Math.min(80, squeeze * 0.64) : 130 + Math.min(110, -squeeze * 0.6));
  const tempSize = Math.round(squeeze >= 0 ? 88 - Math.min(36, squeeze * 0.36) : 88 + Math.min(40, -squeeze * 0.25));
  useLayoutEffect(() => {
    const el = planRef.current;
    if (!el) return;
    const fit = () => {
      const rows = el.querySelectorAll<HTMLElement>(".planrow");
      const rh = rows.length ? Math.max(...Array.from(rows).slice(0, 3).map(r => r.offsetHeight)) : 40;
      const need = Math.min(rows.length, 2) * rh + 2 + (rows[0]?.classList.contains("note") ? rows[0].offsetHeight : 0);
      setPlanMin(m => (Math.abs(m - need) > 1 ? need : m));
      // spare: room the list has beyond two rows; over: how far the whole screen runs past the bottom (then it scrolls)
      const scr = el.closest(".screen") as HTMLElement | null;
      // tablets: the plan + controls column can run past the bottom on its own (the glance column scrolls separately)
      const col = el.closest(".tn-main") as HTMLElement | null;
      const colOver = col && getComputedStyle(col).display !== "contents" && scr
        ? Math.max(0, col.getBoundingClientRect().bottom + parseFloat(getComputedStyle(scr).paddingBottom || "0") - scr.getBoundingClientRect().bottom)
        : 0;
      const over = Math.max(scr ? Math.max(0, scr.scrollHeight - scr.clientHeight) : 0, Math.round(colOver));
      const spare = el.clientHeight - need;
      const allRows = Array.from(rows).reduce((t, r) => t + r.offsetHeight, 0) + 2;
      const spareAll = el.clientHeight - allRows;                // > 0: the whole plan shows and there's still room
      setSqueeze(q => {
        let next: number;
        if (over > 0) next = q + over;                           // runs off the bottom: shrink
        else if (q > 0) next = Math.max(0, q - spare);           // was shrunk and there's room again: give it back
        else if (spareAll > 0) next = q - spareAll;              // everything fits with room left: grow
        else next = Math.min(0, q - spareAll);                   // grew too far and rows got hidden: back off
        next = Math.max(-200, Math.min(150, Math.round(next)));
        return Math.abs(next - q) > 2 && allow(guards.current.squeeze) ? next : q;
      });
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    const scr = el.closest(".screen");
    if (scr) ro.observe(scr);
    return () => ro.disconnect();
  });

  // daytime (the turn-on card instead of the plan): on short screens give height back from the chart and big number
  const [daySq, setDaySq] = useState(0);
  useLayoutEffect(() => {
    const slot = document.querySelector<HTMLElement>(".tn-lower .turnon-slot");
    if (!slot) { if (daySq) setDaySq(0); return; }
    const scr = slot.closest(".screen") as HTMLElement | null;
    if (!scr) return;
    const fit = () => {
      const col = slot.closest(".tn-main") as HTMLElement | null;
      const colOver = col && getComputedStyle(col).display !== "contents"
        ? Math.max(0, col.getBoundingClientRect().bottom + parseFloat(getComputedStyle(scr).paddingBottom || "0") - scr.getBoundingClientRect().bottom) : 0;
      const over = Math.max(0, scr.scrollHeight - scr.clientHeight, Math.round(colOver));
      const used = Array.from(slot.children).reduce((t, c) => t + (c as HTMLElement).offsetHeight, 0) + (slot.children.length - 1) * 12;
      const spare = slot.clientHeight - used;
      setDaySq(q => {
        const next = Math.max(0, Math.min(120, Math.round(over > 0 ? q + over : q > 0 ? q - Math.max(0, spare) : q)));
        return Math.abs(next - q) > 2 && allow(guards.current.day) ? next : q;
      });
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(scr);
    ro.observe(slot);
    return () => ro.disconnect();
  });

  // foldable in tabletop posture: the chart gets exactly what's left of the top half, so nothing sits on the hinge
  const lay = useLayout();
  const upperRef = useRef<HTMLDivElement>(null);
  const [foldH, setFoldH] = useState<number | null>(null);
  const shownH = useRef(0);
  useLayoutEffect(() => {
    if (lay.fold !== "v") { if (foldH !== null) setFoldH(null); return; }
    const up = upperRef.current;
    if (!up) return;
    const next = Math.max(60, Math.min(240, Math.round(lay.segA - 28 - (up.scrollHeight - shownH.current))));
    if ((foldH === null || Math.abs(next - foldH) > 2) && allow(guards.current.fold)) setFoldH(next);
  });

  const night = !!(me && snap && me.settings.simple_at_night && isNight(me, snap));
  const v: View = view ?? me?.settings.default_view ?? "me";
  useEffect(() => { if (night && simple === null) setSimple(true); }, [night, simple]);
  const isSimple = simple === true;
  useEffect(() => { setSimpleOn(isSimple); return () => setSimpleOn(false); }, [isSimple, setSimpleOn]);

  if (!snap || !me) return null;
  const u = me.settings.units;
  if (isSimple) return <Simple person={me} partner={partner} night={night} onExit={() => setSimple(false)} />;

  const focus: PersonView = v === "partner" && partner ? partner : me;
  const target: PersonView = v === "both" ? (snap.people.find(p => p.id === acting) ?? me) : focus;
  const color = (p: PersonView) => (personaColor(p));
  const canAdjust = target.id === me.id || target.settings.partner_can_adjust;
  const canFlash = target.id === me.id || target.settings.partner_can_flash;
  const base = `/api/people/${target.id}`;
  const n = target.night;
  const step = stepC(u);
  const whose = target.id === me.id ? "your" : `${target.name}'s`;
  const run = (path: string, body?: unknown) => { setSheet(false); return act(path, body); };
  const manualOn = !!(n.override?.manual && n.target_c != null);
  const m = target.bed.measured_c;
  const doing = n.target_c == null || m == null ? "On" : n.target_c < m - 0.4 ? "On · Cooling" : n.target_c > m + 0.4 ? "On · Warming" : "On · Holding";
  const idle = n.target_c == null && !n.flash && ["day", "done", "missed", "out_of_bed", "away"].includes(n.state);
  // in bed, but the bed's radar hasn't yet backed it up (core: night.settle.gate === false): the start is held
  const held = n.settle?.gate === false && !n.started && target.presence.present === true;
  const pendingT = onTemp ?? target.phases[0]?.temp_c ?? 21.1;
  const showTurnOn = idle && canAdjust && v !== "both";
  const turnOn = () => { setOnTemp(null); return act(`${base}/adjust`, onLen ? { temp_c: pendingT, minutes: onLen, until: "next_phase" } : { temp_c: pendingT, until: "next_phase" }); };
  const endsAt = onLen ? `until ${clock(snap.now + onLen * 60, snap.tz)}` : "until bedtime";
  const nudge = (sign: number) => showTurnOn
    ? setOnTemp(Math.round((pendingT + sign * step) * 100) / 100)
    : act(`${base}/adjust`, { delta_c: sign * step, until: manualOn ? "next_phase" : until });

  const whoseSeg = (
    <div className="seg" role="group" aria-label="Whose night" data-cycle="v">
      {(["me", "both", "partner"] as View[]).map(k => (
        <button key={k} aria-pressed={v === k} onClick={() => setView(k)} disabled={k !== "me" && !partner}>
          {k === "me" ? "Me" : k === "both" ? "Both" : partner?.name ?? "Partner"}</button>
      ))}
    </div>
  );
  if (v !== "both" && n.guest) return (
    // a side lent to a guest: just the guest's on/off and temperature, no chart, plan or story
    <div className="screen fill tn">
      <div className="tn-main">
        <div className="top">
          <span className="num"><span className="dot" style={{ background: !connected ? "var(--faint)" : snap.system.online ? "var(--good)" : "var(--bad)" }} />{clock(snap.now, snap.tz)}</span>
        </div>
        {whoseSeg}
        <GuestCard person={target} />
      </div>
    </div>
  );

  return (
    <div className="screen fill tn">
      <div className="tn-main">
      <div className="tn-upper" ref={upperRef}>
      <div className="top">
        <span className="num"><span className="dot" style={{ background: !connected ? "var(--faint)" : snap.system.online ? "var(--good)" : "var(--bad)" }} />{clock(snap.now, snap.tz)}{snap.speed !== 1 && snap.system.driver === "sim" ? ` · sim ×${snap.speed}` : ""}</span>
        <button className="icon-btn" aria-label="Night screen: just the temperature and flashes" onClick={() => setSimple(true)}>Night screen</button>
      </div>
      <div className="seg" role="group" aria-label="Whose night" data-cycle="v">
        {(["me", "both", "partner"] as View[]).map(k => (
          <button key={k} aria-pressed={v === k} onClick={() => setView(k)} disabled={k !== "me" && !partner}>
            {k === "me" ? "Me" : k === "both" ? "Both" : partner?.name ?? "Partner"}</button>
        ))}
      </div>
      {v === "me" && !me.presence.present && <PetQuestion person={me.id} />}

      {v !== "both" ? (
        <div className="hero">
          <span className="state">{n.flash && snap.now < n.flash.end ? `${n.flash.kind === "hot" ? "Hot" : "Cold"} flash · then ${n.state === "rhythm" && n.phase_name ? `back to ${n.phase_name}` : "back to off"}` : manualOn ? `${doing} · ${n.override?.end_ts ? `until ${clock(n.override.end_ts, snap.tz)}` : "until bedtime"}` : n.phase_name && n.state === "rhythm" ? n.phase_name : n.state === "waiting" && focus.presence.starts_in_s != null ? "Settling in" : n.state === "paused" && n.pause_until ? `Paused until ${clock(n.pause_until, snap.tz)}` : STATE_TEXT[n.state] ?? n.state}{(() => { const ph = focus.phases.find(p => p.id === n.phase_id); return ph && n.target_c != null && !n.flash && !n.override && Math.abs(ph.temp_c - n.target_c) > 0.25 ? ` · easing to ${tempNum(ph.temp_c, u)}°` : ""; })()}{n.flash && snap.now >= n.flash.end ? " · easing back after the flash" : n.flash ? "" : n.override && !n.override.manual ? ` · adjusted ${n.override.delta_c != null ? deltaText(n.override.delta_c, u) : ""}` : ""}</span>
          <BigTemp c={n.target_c} units={u} color={color(focus)} stale={!connected} size={showTurnOn ? Math.max(46, tempSize - Math.round(daySq * 0.35)) : tempSize} />
          {!connected && <span className="stale-note" role="status">Last updated {clock(snap.now, snap.tz)}</span>}
          <div className="meta">
            {focus.presence.present
              ? <span><span className="dot" />In bed{n.in_bed ? ` ${mins(snap.now - n.in_bed)}` : ""}</span>
              : <span className="faint">{focus.presence.present === false ? "Not in bed" : "Presence unknown"}</span>}
            {focus.bed.measured_c != null && <span>Bed {tempNum(focus.bed.measured_c, u)}°</span>}
            {focus.presence.hr && <span>{focus.presence.hr} bpm</span>}
            {(() => { const t = contextText(n, snap.now, snap.tz); return t ? <span>{t}</span> : null; })()}
          </div>
        </div>
      ) : (
        <div className="sides">
          {snap.people.map(p => (
            <button key={p.id} className="side" aria-pressed={target.id === p.id} style={{ ["--selc" as string]: color(p) }} onClick={() => setActing(p.id)}>
              <span style={{ fontSize: 13 }}>{p.name}{p.persona !== "neutral" && <span className={`persona ${p.persona}`}>{p.persona}</span>}</span>
              <span className="t"><BigTemp c={p.night.target_c} units={u} color={color(p)} /></span>
              <small>{p.presence.present ? "In bed" : "Not in bed"} · {p.night.phase_name ?? STATE_TEXT[p.night.state]}</small>
            </button>
          ))}
        </div>
      )}

      <div className="chart" ref={chartRef}>
        <Horizon width={chartW} realSize people={v === "both" ? snap.people : [focus]} now={snap.now} tz={snap.tz} units={u} gradient={v !== "both"} height={(shownH.current = foldH ?? (v === "both" ? Math.min(120, chartH) : showTurnOn ? Math.max(40, (roomy ? Math.min(240, Math.round(window.innerHeight * 0.26)) : Math.min(96, chartH)) - Math.round(daySq * 0.7)) : chartH))} />
        {!(showTurnOn && daySq > 30) && <div className="chartkey"><span><i className="k-plan" />plan</span><span><i className="k-ahead" />ahead</span><span><i className="k-bed" />bed's actual</span><span><i className="k-now" />now</span></div>}
      </div>
      </div>
      <div className="tn-lower">

      <AwayNote person={target} me={me} isOwner={isOwner} tz={snap.tz} />
      <CareTonight running={snap.people.some(p => p.night.state === "rhythm" || p.night.state === "out_of_bed_hold")} />
      {n.flash && snap.now < n.flash.end ? (
        <div className="flashcard" style={{ borderColor: n.flash.kind === "hot" ? "var(--cool)" : "var(--warm)" }}>
          <span><span style={{ display: "inline-flex", alignItems: "center", gap: 6, verticalAlign: "-3px" }}><Icon name={n.flash.kind === "hot" ? "snow" : "sun"} size={17} />{n.flash.kind === "hot" ? "Hot flash" : "Cold flash"}</span>{target.id !== me.id ? ` · ${target.name}` : ""}<br />
            <span className="mute num" style={{ fontSize: 13 }}>{snap.now < n.flash.end ? `${mmss(n.flash.end - snap.now)} left` : "easing back"}</span></span>
          <span className="pills"><button className="pill" onClick={() => act(`${base}/flash/extend`)}>+5 min</button><button className="pill" onClick={() => act(`${base}/flash/stop`)}>Stop</button></span>
        </div>
      ) : showTurnOn ? (
        <div className="turnon-slot">
          {target.id === me.id && <TodayCard me={me} snap={snap} />}
          <TurnOn t={pendingT} units={u} len={onLen} setLen={setOnLen} endsAt={endsAt} onStart={turnOn} connected={connected} />
        </div>
      ) : manualOn && v !== "both" ? (
        <div className="flashcard" style={{ borderColor: color(target) }}>
          <span>{doing}{target.id !== me.id ? ` · ${target.name}` : ""}<br />
            <span className="mute num" style={{ fontSize: 13 }}>{n.override?.end_ts ? `${mins(n.override.end_ts - snap.now)} left, then off` : "Until your rhythm starts tonight"}</span></span>
          <span className="pills">{n.override?.end_ts && <button className="pill" onClick={() => act(`${base}/adjust`, { delta_c: 0, minutes: Math.round((n.override!.end_ts! - snap.now) / 60) + 30, until: "next_phase" })}>+30 min</button>}
            <button className="pill" onClick={() => act(`${base}/adjust/clear`)}>Turn off</button></span>
        </div>
      ) : (
        <div className="plan" ref={planRef} style={{ minHeight: planMin || undefined }}>
          {n.state === "waiting" && (held
            ? <div className="planrow note">Waiting until {target.id === me.id ? "you're" : `${target.name} is`} settled in.</div>
            : target.presence.starts_in_s != null
            ? <div className="planrow note">{target.id === me.id ? "You're" : `${target.name}'s`} in bed. The night starts in {Math.max(1, Math.ceil(target.presence.starts_in_s / 60))} min, once settled.</div>
            : <div className="planrow note">Holding the pre-cool. The night starts when {target.id === me.id ? "you're" : `${target.name} is`} in bed{target.schedule ? ` (from ${clock(target.schedule.window_start, snap.tz)})` : ""}.</div>)}
          {v === "both" && <div className="planrow note" style={{ paddingBottom: 2 }}>{target.id === me.id ? "Your" : `${target.name}'s`} next changes · tap a side above to switch</div>}
          {!n.away && n.state === "away" && <div className="planrow note">Waiting until {target.id === me.id ? "you're" : `${target.name} is`} home</div>}
          {target.phases.filter((_p, i, arr) => (arr[i + 1]?.start ?? Infinity) > snap.now).map(p => {
            const cur = p.id === n.phase_id;
            return <div key={p.id} className={`planrow ${cur ? "cur" : ""}`}>
              <span className="num tm">{cur ? "now" : clock(p.start, snap.tz)}</span>
              <span>{p.name}</span>
              <span className="num">{p.adjusted_c != null
                ? <>{tempNum(p.adjusted_c, u)}°<small className="mute" style={{ marginLeft: 6, fontSize: 11 }}>usually {tempNum(p.temp_c, u)}°</small></>
                : <>{tempNum(p.temp_c, u)}°</>}</span>
            </div>;
          })}
          {target.schedule && <div className="planrow"><span className="num tm">{clock(target.schedule.end, snap.tz)}</span><span>Side off</span><span /></div>}
        </div>
      )}

      <div className="dock">
        {/* right-handed: the temperature (used most) sits under the thumb on the right, flashes on the left */}
        <div className="dockctl">
          <div className="flashes">
            <button className="hot" disabled={!canFlash || !connected} onClick={() => act(`${base}/flash`, { kind: "hot" })}><Icon name="snow" className="fl-ico" /> Hot flash</button>
            <button className="cold" disabled={!canFlash || !connected} onClick={() => act(`${base}/flash`, { kind: "cold" })}><Icon name="sun" className="fl-ico" /> Cold flash</button>
          </div>
          <div className="steps">
            <button aria-label="Warmer" disabled={!connected || !canAdjust || (!showTurnOn && n.target_c == null && n.state !== "paused" && n.state !== "skipped")} onClick={() => nudge(1)}>+<small>Warmer</small></button>
            <button aria-label="Cooler" disabled={!connected || !canAdjust || (!showTurnOn && n.target_c == null && n.state !== "paused" && n.state !== "skipped")} onClick={() => nudge(-1)}>−<small>Cooler</small></button>
          </div>
        </div>
        {!canAdjust && <p className="mute" style={{ fontSize: 12, textAlign: "center", margin: "-4px 0 0" }}>Only flashes: {target.name} hasn't shared temperature control.</p>}
        {(n as { room_note?: { text: string } | null }).room_note && <p className="mute" style={{ fontSize: 12, textAlign: "center", margin: "-4px 0 0" }}>{(n as { room_note?: { text: string } | null }).room_note!.text}.</p>}
        <div className="sub">
          {showTurnOn || manualOn ? <span /> : <button className="link" onClick={() => setUntil(until === "night" ? "next_phase" : "night")}>
            Changes last {until === "night" ? "the rest of the night" : "until the next phase"}</button>}
          <button className="icon-btn" data-key="s" data-key-always="" onClick={() => setSheet(true)}>Tonight ⋯</button>
        </div>
      </div>
      </div>

      </div>
      <Glance snap={snap} me={me} partner={partner ?? null} />
      {sheet && <Sheet onClose={() => setSheet(false)} role="dialog" aria-label={`Tonight for ${target.name}`}>
          <div className="k">Tonight · {target.name}</div>
          {target.id === me.id && <div style={{ display: "grid", gap: 8, padding: "6px 0 10px", borderBottom: "1px solid var(--line)" }}>
            <span style={{ fontSize: 14 }}>How does it feel right now?<small className="mute" style={{ display: "block", fontSize: 12 }}>Doesn't change anything by itself. Autopilot learns from it.</small></span>
            <div className="chips">{([["too_cold", "Too cold"], ["just_right", "Just right"], ["too_hot", "Too hot"]] as const).map(([k, l]) =>
              <button key={k} className="chip" onClick={async () => { setSheet(false); try { await api.post(`/api/people/${me.id}/feel`, { feel: k }); say(k === "just_right" ? "Noted" : `Noted. ${k === "too_hot" ? "Cooler" : "Warmer"} is one tap away if you want it now.`); } catch { say("Couldn't save that"); } }}>{l}</button>)}</div>
          </div>}
          {canAdjust && n.override && <button className="item" onClick={() => run(`${base}/adjust/clear`)}>Undo the change<small>back to the rhythm</small></button>}
          {canAdjust && !n.started && (n.state === "waiting" || n.state === "away") && <button className="item" onClick={() => run(`${base}/tonight`, { action: "start_now" })}>Start the rhythm now<small>for reading in bed</small></button>}
          {!canAdjust && <p className="mute" style={{ fontSize: 13, padding: "8px 0" }}>{target.name} hasn't shared control of their side. They can turn it on in their Settings → Sharing.</p>}
          {canAdjust && n.context?.early_start && target.schedule && target.schedule.wake > n.context.early_start - 2700 && (() => {
            const w = n.context!.early_start! - 45 * 60;                 // warm wake 45 min before the early start
            return <button className="item" onClick={() => run(`${base}/context`, { wake: w })}>Wake up earlier tonight<small>early start at {clock(n.context!.early_start!, snap.tz)} · warm wake from {clock(w - 30 * 60, snap.tz)}</small></button>;
          })()}
          {(canAdjust || target.id === me.id || isOwner) && <div className="k" style={{ padding: "10px 2px 0" }}>Not sleeping here?</div>}
          {/* shortest to longest: an hour, tonight, a few days */}
          {canAdjust && (n.state === "paused"
            ? <button className="item" onClick={() => run(`${base}/tonight`, { action: "resume" })}>Resume now<small>paused until {clock(n.pause_until, snap.tz)}</small></button>
            : <button className="item" onClick={() => run(`${base}/tonight`, { action: "pause", minutes: 60 })}>Pause for an hour<small>{whose} side turns off, then back on by itself</small></button>)}
          {canAdjust && (n.skip
            ? <button className="item" onClick={() => run(`${base}/tonight`, { action: "resume" })}>Turn {whose} side back on<small /></button>
            : <button className="item" onClick={() => run(`${base}/tonight`, { action: "skip" })}>Off tonight<small>{whose} side stays off until tomorrow evening</small></button>)}
          {(target.id === me.id || isOwner) && (n.away
            ? <button className="item" onClick={() => run(`${base}/away`, { away: false })}>{target.id === me.id ? "I'm back" : `${target.name} is back`}<small>{whose} nights run again from now</small></button>
            : <button className="item" onClick={() => { run(`${base}/away`, { away: true }); say(target.id === me.id ? "You're marked away. Your side stays off until you're back." : `${target.name} is marked away. Their side stays off until they're back.`); }}>Away for a few days<small>{whose} side stays off until {target.id === me.id ? "you tap I'm back" : "marked back"}</small></button>)}
          {isOwner && <button className="item" onClick={() => { setSheet(false); setGuestSheet(true); }}>Guest mode<small>lend a side, or the whole bed, to a guest</small></button>}
          <button className="item" onClick={() => setSheet(false)}>Cancel<small /></button>
      </Sheet>}
      {guestSheet && <Sheet onClose={() => setGuestSheet(false)} role="dialog" aria-label="Guest mode">
        <div className="k">Guest mode</div>
        <GuestStart onDone={() => setGuestSheet(false)} />
      </Sheet>}
    </div>
  );
}
