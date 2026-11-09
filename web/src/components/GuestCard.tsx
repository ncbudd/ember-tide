import { useRef, useState } from "react";
import { useAuth } from "../auth";
import { useApp } from "../ctx";
import { personaColor, stepC, tempNum } from "../format";
import type { GuestSide, PersonView } from "../types";
import BedSidePicker, { sideTitle } from "./BedSidePicker";
import Tip from "./Tip";
import Icon from "./Icon";

const sideName = (z: string) => sideTitle(z === "right" ? "right" : "left");

/** A side lent to a guest: one temperature, warmer / cooler, on / off. Nothing else, and nothing is recorded. */
export function GuestCard({ person }: { person: PersonView }) {
  const { act, me } = useApp();
  const { isOwner } = useAuth();
  const g = person.night.guest as GuestSide;
  const u = me?.settings.units ?? "F";
  const zone = person.zone;
  const set = (body: { on?: boolean; temp_c?: number }) => act(`/api/guest/${zone}/set`, body);
  // quick taps add up: each one steps from the last value asked for, not the (lagging) live one
  const pending = useRef<{ c: number; at: number } | null>(null);
  const nudge = (sign: number) => {
    const base = pending.current && Date.now() - pending.current.at < 4000 ? pending.current.c : g.temp_c;
    const c = Math.min(43, Math.max(13, Math.round((base + sign * stepC(u)) * 100) / 100));
    pending.current = { c, at: Date.now() };
    set({ temp_c: c, ...(g.on ? {} : { on: true }) });
  };
  const shown = pending.current && Date.now() - pending.current.at < 4000 ? pending.current.c : g.temp_c;
  return (
    <div className="guest-card" aria-label={`${sideName(zone)} guest controls`}>
      <span className="k">{sideName(zone)} · guest</span>
      <div className="guest-temp">
        {g.on
          ? <span className="big">{tempNum(shown, u)}<span className="deg">°</span></span>
          : <span className="big off" style={{ color: "var(--mute)" }}>Off</span>}
        <span className="mute guest-line">
          {g.on ? `Turns off ${g.morning_off_min} min after they get up in the morning` : `Was ${tempNum(g.temp_c, u)}° · turn it on any time`}
        </span>
      </div>
      <div className="guest-ctl">
        <button className={`guest-power${g.on ? " on" : ""}`} onClick={() => set({ on: !g.on })} aria-pressed={g.on}>
          <Icon name="power" size={22} /> {g.on ? "Turn off" : "Turn on"}
        </button>
        <div className="guest-steps">
          <button aria-label="Warmer" onClick={() => nudge(1)}>+<small>Warmer</small></button>
          <button aria-label="Cooler" onClick={() => nudge(-1)}>−<small>Cooler</small></button>
        </div>
      </div>
      <p className="faint guest-note">Guest mode keeps no record of the night. {isOwner
        ? <button className="link" onClick={() => act(`/api/guest/${zone}/stop`)}>End guest mode</button>
        : "The person who set it up can end it."}</p>
    </div>
  );
}

/** Start guest mode: which side, a temperature, and how soon it turns off after they get up. Owner only. */
export function GuestStart({ onDone }: { onDone?: () => void }) {
  const { act, snap, me, say } = useApp();
  const u = me?.settings.units ?? "F";
  const [zone, setZone] = useState<"left" | "right" | "both">(() => (me?.zone === "left" ? "right" : "left"));
  const [temp, setTemp] = useState(21.0);
  const [mins, setMins] = useState(40);
  const active = Object.keys(snap?.guest ?? {});
  const start = async () => {
    await act(`/api/guest/${zone}/start`, { temp_c: temp, morning_off_min: mins });
    say(zone === "both" ? "Whole bed in guest mode" : `${sideName(zone)} in guest mode`);
    onDone?.();
  };
  return (
    <div className="guest-start">
      <p className="mute" style={{ fontSize: 13.5 }}>
        A guest gets a plain on / off and temperature, on the app and the bedside dial.
        <Tip label="What is guest mode?" text="No schedule, no Autopilot, and nothing about their night is kept. It turns itself off in the morning once they're up." />
      </p>
      <BedSidePicker value={zone} onChange={setZone} whole="Whole bed" label="Which side is the guest's"
        caption="As you lie in bed, which side is the guest's? Tap it, or pick the whole bed."
        halves={Object.fromEntries((["left", "right"] as const).map(z => { const p = snap?.people.find(x => x.zone === z);
          return [z, { name: zone === z || zone === "both" ? "Guest" : p?.name, color: zone === z || zone === "both" ? "var(--acc)" : p ? personaColor(p) : undefined }]; }))} />
      <div className="row">
        <span>Temperature</span>
        <span className="stepper">
          <button aria-label="Cooler" onClick={() => setTemp(t => Math.max(13, Math.round((t - stepC(u)) * 100) / 100))}>−</button>
          <span className="v num">{tempNum(temp, u)}°</span>
          <button aria-label="Warmer" onClick={() => setTemp(t => Math.min(43, Math.round((t + stepC(u)) * 100) / 100))}>+</button>
        </span>
      </div>
      <div className="row">
        <span>Turn off after they get up</span>
        <div className="chips">{[30, 40, 45].map(m => <button key={m} className="chip" aria-pressed={mins === m} onClick={() => setMins(m)}>{m} min</button>)}</div>
      </div>
      {active.length > 0 && <p className="faint" style={{ fontSize: 12.5 }}>Already a guest on: {active.map(sideName).join(", ")}.</p>}
      <button className="btn pri big" onClick={start}>Start guest mode</button>
    </div>
  );
}
