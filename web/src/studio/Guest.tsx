import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import BedSidePicker, { sideTitle, type Side, useBedFromFoot } from "../components/BedSidePicker";
import Tip from "../components/Tip";
import { useApp } from "../ctx";
import { personaColor, stepC, tempNum, h12 } from "../format";
import type { GuestSide, Snapshot } from "../types";

/**
 * Studio → Guest mode: lend a side (or the whole bed) to a guest, with every detail: side, temperature, how soon it
 * turns off once they're up, an optional end, and whether the bedside dial shows its guest screen.
 * The Overview shows only GuestBanner, and only while a guest is staying. (The phone keeps components/GuestCard.)
 */

const MINS = [30, 40, 45];

/** "YYYY-MM-DDTHH:MM" in the household's time zone ⇄ unix seconds. */
function toInput(ts: number, tz: string): string {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(ts * 1000)).map(x => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}
function fromInput(v: string, tz: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(v);
  if (!m) return null;
  const guess = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 1000;
  const back = toInput(guess, tz);                      // what that instant reads as in the household's zone
  const b = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(back)!;
  const offset = Date.UTC(+b[1], +b[2] - 1, +b[3], +b[4], +b[5]) / 1000 - guess;
  return guess - offset;
}
const endText = (ts: number, tz: string) => new Date(ts * 1000).toLocaleString([], { ...h12(), timeZone: tz, weekday: "short", hour: "numeric", minute: "2-digit" });

/** The guests staying now, grouped: the whole bed when both sides match, else each side. */
function staying(snap: Snapshot): { zones: Side[]; g: GuestSide }[] {
  const e = (["left", "right"] as const).flatMap(z => snap.guest?.[z] ? [{ z, g: snap.guest[z]! }] : []);
  if (e.length === 2 && e[0].g.on === e[1].g.on && Math.abs(e[0].g.temp_c - e[1].g.temp_c) < 0.01 && e[0].g.morning_off_min === e[1].g.morning_off_min)
    return [{ zones: ["left", "right"], g: e[0].g }];
  return e.map(x => ({ zones: [x.z], g: x.g }));
}
const where = (zones: Side[]) => zones.length === 2 ? "Whole bed" : sideTitle(zones[0]);
const path = (zones: Side[]) => zones.length === 2 ? "both" : zones[0];

/** Overview: one line while a guest is staying, nothing otherwise. */
export function GuestBanner() {
  const { snap, me, say } = useApp();
  const { isOwner } = useAuth();
  useBedFromFoot();
  if (!snap) return null;
  const list = staying(snap);
  if (!list.length) return null;
  const u = me?.settings.units ?? "F";
  return (
    <div className="box guest-banner" role="status" style={{ borderColor: "color-mix(in srgb, var(--acc) 55%, var(--line))", background: "color-mix(in srgb, var(--acc) 8%, var(--surf))", padding: "10px 16px" }}>
      {list.map(({ zones, g }) => (
        <div className="row" key={zones.join()} style={{ flexWrap: "wrap" }}>
          <span><b style={{ fontWeight: 500 }}>Guest mode on</b> · {where(zones)} · {g.on ? `${tempNum(g.temp_c, u)}°` : "side off for now"} · turns off {g.morning_off_min} min after they get up
            {g.ends ? <span className="mute"> · ends {endText(g.ends, snap.tz)}</span> : null}</span>
          <span className="row" style={{ gap: 10 }}>
            <Link to="/studio/guest" style={{ fontSize: 13 }}>Guest settings</Link>
            {isOwner && <button type="button" className="pill" onClick={async () => {
              try { await api.post(`/api/guest/${path(zones)}/stop`); say("Guest mode off"); } catch (e) { say((e as Error).message); }
            }}>Turn off</button>}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function GuestPage() {
  const { snap, me } = useApp();
  const { isOwner } = useAuth();
  useBedFromFoot();
  if (!snap) return null;
  const list = staying(snap);
  const u = me?.settings.units ?? "F";
  const free = (["left", "right"] as const).filter(z => !snap.guest?.[z]);
  return (
    <>
      <div><div className="k">Live</div><h1 style={{ fontSize: 34 }}>Guest mode</h1></div>
      <div className="box">
        <span style={{ fontSize: 14 }}>Lend your side, or the whole bed, to a guest.
          <Tip label="What is guest mode?" text="While it's on, that side's own rhythm, Autopilot and sensors rest. Your sleep history skips those nights, so a guest doesn't change what Ember & Tide has learned about you." /></span>
        <span className="mute" style={{ fontSize: 13 }}>A guest gets a plain on / off and one temperature, in the app and on the bedside dial. No schedule, no Autopilot, and nothing about their night is kept. It turns itself off in the morning once they've been up a while.</span>
        {!isOwner && <span className="faint" style={{ fontSize: 12.5 }}>Only the owner can start or end guest mode. Anyone can turn a guest's side on or off and change its temperature.</span>}
      </div>
      <div className="grid2 even">
        {list.map(({ zones, g }) => <Staying key={zones.join()} zones={zones} g={g} tz={snap.tz} now={snap.now} units={u} owner={isOwner} />)}
        {isOwner && free.length > 0 && <Start free={free} />}
      </div>
    </>
  );
}

/** A guest staying now: everything adjustable in place. */
function Staying({ zones, g, tz, now, units, owner }: { zones: Side[]; g: GuestSide; tz: string; now: number; units: "F" | "C"; owner: boolean }) {
  const { say } = useApp();
  const [askEnd, setAskEnd] = useState(false);
  const set = async (body: object, done = "Saved") => {
    try { await api.post(`/api/guest/${path(zones)}/set`, body); say(done); } catch (e) { say(`Couldn't change it: ${(e as Error).message}`); }
  };
  const pending = useRef<{ c: number; at: number } | null>(null);      // quick taps add up (see GuestCard)
  const nudge = (sign: number) => {
    const base = pending.current && Date.now() - pending.current.at < 4000 ? pending.current.c : g.temp_c;
    const c = Math.min(43, Math.max(13, Math.round((base + sign * stepC(units)) * 100) / 100));
    pending.current = { c, at: Date.now() };
    void set({ temp_c: c });
  };
  const shownC = pending.current && Date.now() - pending.current.at < 4000 ? pending.current.c : g.temp_c;
  return (
    <div className="box">
      <div className="row"><span className="k" style={{ margin: 0 }}>{where(zones)} · guest</span>
        <span className="mute" style={{ fontSize: 12.5 }}>since {endText(g.since, tz)}</span></div>
      <div className="list">
        <div className="row"><span>Side is</span>
          <div className="seg" style={{ width: 150 }} role="group" aria-label="On or off">
            <button type="button" aria-pressed={g.on} onClick={() => set({ on: true }, "On")}>On</button>
            <button type="button" aria-pressed={!g.on} onClick={() => set({ on: false }, "Off")}>Off</button>
          </div></div>
        <div className="row"><span>Temperature</span>
          <span className="stepper">
            <button type="button" aria-label="Cooler" onClick={() => nudge(-1)}>−</button>
            <span className="v num">{tempNum(shownC, units)}°</span>
            <button type="button" aria-label="Warmer" onClick={() => nudge(1)}>+</button>
          </span></div>
        <div className="row"><span>Turn off after they get up<Tip label="Turning off in the morning" text="Once the side has been empty this long in the morning, it turns off by itself. Getting up in the night doesn't count." /></span>
          <div className="chips">{MINS.map(m => <button key={m} type="button" className="chip" aria-pressed={g.morning_off_min === m} onClick={() => set({ morning_off_min: m })}>{m} min</button>)}</div></div>
        <EndRow ends={g.ends} tz={tz} now={now} onChange={ends => set({ ends: ends ?? 0 }, ends ? `Ends ${endText(ends, tz)}` : "Stays until you end it")} />
        <div className="row"><span>Bedside dial shows guest mode<br /><small className="mute">Just the temperature and on / off. Off: the dial keeps its usual look.</small></span>
          <input type="checkbox" className="toggle" aria-label="Bedside dial shows guest mode" checked={g.dial ?? true} onChange={e => set({ dial: e.target.checked })} /></div>
      </div>
      {owner && (askEnd
        ? <div className="row" role="alertdialog" aria-label="End guest mode"><span>End guest mode{zones.length === 2 ? " for the whole bed" : ""}?</span>
            <span className="pills"><button type="button" className="btn" onClick={() => setAskEnd(false)}>Keep</button>
              <button type="button" className="btn pri" onClick={async () => {
                try { await api.post(`/api/guest/${path(zones)}/stop`); say("Guest mode off"); } catch (e) { say((e as Error).message); }
                setAskEnd(false);
              }}>End it</button></span></div>
        : <div className="row" style={{ justifyContent: "flex-end" }}><button type="button" className="btn" onClick={() => setAskEnd(true)}>End guest mode</button></div>)}
    </div>
  );
}

/** Optional end: "Until you end it" or a date and time (within 30 days). */
function EndRow({ ends, tz, now, onChange }: { ends: number | null; tz: string; now: number; onChange: (ends: number | null) => void }) {
  const [draft, setDraft] = useState(() => toInput(ends ?? now + 86400, tz));
  const [picking, setPicking] = useState(ends != null);
  const bad = (ts: number | null) => ts == null || ts <= now || ts > now + 30 * 86400;
  return (
    <div className="item" style={{ display: "grid", gap: 8 }}>
      <span className="row"><span>Ends</span>
        <div className="seg" style={{ width: 220 }} role="group" aria-label="When it ends">
          <button type="button" aria-pressed={!picking} onClick={() => { setPicking(false); if (ends != null) onChange(null); }}>When I end it</button>
          <button type="button" aria-pressed={picking} onClick={() => setPicking(true)}>At a time</button>
        </div></span>
      {picking && <div className="row" style={{ justifyContent: "flex-end", flexWrap: "wrap" }}>
        <input type="datetime-local" aria-label="End date and time" value={draft} onChange={e => setDraft(e.target.value)} />
        <button type="button" className="btn" disabled={bad(fromInput(draft, tz))} onClick={() => onChange(fromInput(draft, tz))}>{ends ? "Change" : "Set"}</button>
      </div>}
      {picking && bad(fromInput(draft, tz)) && <span className="faint" style={{ fontSize: 12.5, textAlign: "right" }}>Pick a time in the next 30 days.</span>}
      {ends != null && <span className="mute" style={{ fontSize: 12.5, textAlign: "right" }}>Guest mode ends {endText(ends, tz)}.</span>}
    </div>
  );
}

/** Start: side or whole bed, temperature, morning turn-off, optional end, dial. */
function Start({ free }: { free: Side[] }) {
  const { snap, me, say } = useApp();
  const u = me?.settings.units ?? "F";
  const both = free.length === 2;
  const [picked, setZone] = useState<Side | "both">(() => (free.includes(me?.zone === "left" ? "right" : "left") ? (me?.zone === "left" ? "right" : "left") : free[0]));
  // a guest just started on one side: whatever was picked falls back to the side that's still free
  const zone: Side | "both" = picked === "both" ? (both ? "both" : free[0]) : free.includes(picked) ? picked : free[0];
  const [temp, setTemp] = useState(21.0);
  const [mins, setMins] = useState(40);
  const [endOn, setEndOn] = useState(false);
  const [endDraft, setEndDraft] = useState(() => snap ? toInput(snap.now + 86400, snap.tz) : "");
  const [dial, setDial] = useState(true);
  const [busy, setBusy] = useState(false);
  if (!snap) return null;
  const ends = endOn ? fromInput(endDraft, snap.tz) : null;
  const endBad = endOn && (ends == null || ends <= snap.now || ends > snap.now + 30 * 86400);
  const start = async () => {
    setBusy(true);
    try {
      await api.post(`/api/guest/${zone}/start`, { temp_c: temp, morning_off_min: mins, ends, dial });
      say(zone === "both" ? "Whole bed in guest mode" : `${sideTitle(zone)} in guest mode`);
    } catch (e) { say(`Couldn't start: ${(e as Error).message}`); }
    setBusy(false);
  };
  return (
    <div className="box">
      <span className="k">{both ? "Start guest mode" : "Lend the other side too"}</span>
      <div style={{ maxWidth: 380 }}>
        <BedSidePicker value={zone} whole={both ? "Whole bed" : undefined} label="Which side is the guest's"
          onChange={z => { if (z === "both" ? both : free.includes(z)) setZone(z); }}
          caption={both ? "As you lie in bed, which side is the guest's? Tap it, or pick the whole bed." : "The other side already has a guest."}
          halves={Object.fromEntries((["left", "right"] as const).map(z => {
            const p = snap.people.find(x => x.zone === z);
            const guest = zone === z || zone === "both" || !free.includes(z);
            return [z, { name: guest ? "Guest" : p?.name, color: guest ? "var(--acc)" : p ? personaColor(p) : undefined }];
          }))} />
      </div>
      <div className="list">
        <div className="row"><span>Temperature</span>
          <span className="stepper">
            <button type="button" aria-label="Cooler" onClick={() => setTemp(t => Math.max(13, Math.round((t - stepC(u)) * 100) / 100))}>−</button>
            <span className="v num">{tempNum(temp, u)}°</span>
            <button type="button" aria-label="Warmer" onClick={() => setTemp(t => Math.min(43, Math.round((t + stepC(u)) * 100) / 100))}>+</button>
          </span></div>
        <div className="row"><span>Turn off after they get up</span>
          <div className="chips">{MINS.map(m => <button key={m} type="button" className="chip" aria-pressed={mins === m} onClick={() => setMins(m)}>{m} min</button>)}</div></div>
        <div className="item" style={{ display: "grid", gap: 8 }}>
          <span className="row"><span>Ends</span>
            <div className="seg" style={{ width: 220 }} role="group" aria-label="When it ends">
              <button type="button" aria-pressed={!endOn} onClick={() => setEndOn(false)}>When I end it</button>
              <button type="button" aria-pressed={endOn} onClick={() => setEndOn(true)}>At a time</button>
            </div></span>
          {endOn && <div className="row" style={{ justifyContent: "flex-end" }}><input type="datetime-local" aria-label="End date and time" value={endDraft} onChange={e => setEndDraft(e.target.value)} /></div>}
          {endBad && <span className="faint" style={{ fontSize: 12.5, textAlign: "right" }}>Pick a time in the next 30 days.</span>}
        </div>
        <div className="row"><span>Bedside dial shows guest mode<br /><small className="mute">Just the temperature and on / off.</small></span>
          <input type="checkbox" className="toggle" aria-label="Bedside dial shows guest mode" checked={dial} onChange={e => setDial(e.target.checked)} /></div>
      </div>
      <button type="button" className="btn pri big" disabled={busy || endBad} onClick={start}>{busy ? "Starting…" : "Start guest mode"}</button>
    </div>
  );
}
