import Icon from "../components/Icon";
import { useApp } from "../ctx";
import { clock, deltaText, fToC, mmss, personaColor, stepC, tempNum } from "../format";
import type { PersonView } from "../types";

const LO = fToC(55), HI = fToC(90);

/** In bed / middle of the night: pure black, one temperature, + and − under the right thumb, two flashes on the left. */
export default function Simple({ person, partner, onExit, night }: { person: PersonView; partner?: PersonView; onExit: () => void; night: boolean }) {
  const { snap, act } = useApp();
  if (!snap) return null;
  const u = person.settings.units;
  const n = person.night;
  const f = n.flash && snap.now < n.flash.end ? n.flash : null;     // easing back after a flash counts as normal
  const easing = !f && n.flash ? n.flash : null;
  const off = n.target_c == null;
  const shown = f ? f.target_c : n.target_c;
  const base = `/api/people/${person.id}`;
  const step = stepC(u);
  const mine = personaColor(person);

  const R = 46, C = 50;
  const pt = (a: number) => [C + R * Math.cos((a * Math.PI) / 180), C + R * Math.sin((a * Math.PI) / 180)];
  const arc = (a0: number, a1: number) => { const [x0, y0] = pt(a0), [x1, y1] = pt(a1); return `M${x0},${y0} A${R},${R} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1},${y1}`; };
  const frac = shown == null ? 0 : Math.min(1, Math.max(0, (shown - LO) / (HI - LO)));
  const flashFrac = f ? Math.max(0, Math.min(1, (f.end - snap.now) / Math.max(1, f.end - f.start))) : 0;
  const ring = f ? (f.kind === "hot" ? "var(--cool)" : "var(--warm)") : mine;
  const knob = pt(135 + 270 * frac);
  const resumable = n.state === "paused" || n.state === "skipped";
  const adjust = (sign: number) =>
    act(`${base}/adjust`, off && !resumable ? { temp_c: (person.bed.setpoint_c ?? fToC(70)) + sign * step, until: "next_phase" } : { delta_c: sign * step, until: "next_phase" });

  // one short status line, and (when + or − would do something unexpected) a quieter hint under it
  const status = f
    ? `${f.kind === "hot" ? "Hot" : "Cold"} flash · ${mmss(f.end - snap.now)} left`
    : easing && n.base_c != null ? `Easing back to ${tempNum(n.base_c + (n.override?.delta_c ?? 0), u)}°`
    : n.state === "paused" && n.pause_until ? `Paused until ${clock(n.pause_until, snap.tz)}`
    : off ? (n.skip ? "Off for tonight" : "Your side is off")
    : n.override ? (n.override.temp_c != null && !n.phase_name ? "Turned on by hand" : `${n.override.delta_c != null ? deltaText(n.override.delta_c, u) : "Changed"} until the next phase`)
    : n.phase_name ?? "";
  const hint = !f && (n.state === "paused" || (off && n.skip)) ? "+ or − turns it back on" : !f && off ? "+ or − turns it on" : "";
  const extra = person.settings[f?.kind === "cold" ? "cold_flash" : "hot_flash"].extend_min;

  return (
    <div className="simple">
      <div className="top">
        <span className="num">{clock(snap.now, snap.tz)}</span>
        <span>{person.name}{partner && partner.night.target_c != null ? <span className="faint"> · {partner.name} {tempNum(partner.night.target_c, u)}°</span> : null}</span>
      </div>

      <div className="mid">
        <div className="dial">
          <svg viewBox="0 0 100 100" aria-hidden="true">
            <path d={arc(135, 405)} fill="none" stroke="var(--line2)" strokeWidth="1.8" strokeLinecap="round" />
            {f
              ? <path d={arc(135, 135 + 270 * Math.max(0.001, flashFrac))} fill="none" stroke={ring} strokeWidth="2.4" strokeLinecap="round" />
              : !off && <>
                  <path d={arc(135, 135 + 270 * Math.max(0.001, frac))} fill="none" stroke={ring} strokeWidth="2.4" strokeLinecap="round" opacity=".85" />
                  <circle cx={knob[0]} cy={knob[1]} r="2.8" fill="var(--text)" />
                </>}
          </svg>
          <div className="center">
            {off && !f ? <span className="big t" style={{ color: "var(--mute)" }}>Off</span>
              : <span className="big t" style={{ color: f ? ring : "var(--text)" }}>{tempNum(shown, u)}<span className="deg">°</span></span>}
            <span className="s">{status}{hint && <small>{hint}</small>}</span>
          </div>
        </div>
      </div>

      <div className="controls">
        <div className="col">
          {f ? <>
            <button onClick={() => act(`${base}/flash/extend`)}>+{extra} min</button>
            <button onClick={() => act(`${base}/flash/stop`)}>Stop</button>
          </> : <>
            <button className="hot" onClick={() => act(`${base}/flash`, { kind: "hot" })}><Icon name="snow" className="fl-ico" /> Hot flash</button>
            <button className="cold" onClick={() => act(`${base}/flash`, { kind: "cold" })}><Icon name="sun" className="fl-ico" /> Cold flash</button>
          </>}
        </div>
        <div className="steps">
          <button aria-label="Warmer" disabled={!!f} onClick={() => adjust(1)}>+</button>
          <button aria-label="Cooler" disabled={!!f} onClick={() => adjust(-1)}>−</button>
        </div>
      </div>

      <div className="foot">
        {n.state === "paused" ? <button className="link" onClick={() => act(`${base}/tonight`, { action: "resume" })}>Resume now</button>
          : n.skip ? <button className="link" onClick={() => act(`${base}/tonight`, { action: "resume" })}>Turn my side back on</button>
          : <button className="link" onClick={() => act(`${base}/tonight`, { action: "skip" })}>Off tonight</button>}
        {n.override && <button className="link" onClick={() => act(`${base}/adjust/clear`)}>Undo change</button>}
        <button className="link" onClick={onExit}>{night ? "Full app" : "Show chart"}</button>
      </div>
    </div>
  );
}
