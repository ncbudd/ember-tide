import { useEffect, useState } from "react";
import { api } from "../api";
import type { PersonSettings } from "../types";

/**
 * Which notifications a person gets, and how they're worded. Shared by the phone's Settings and Studio → Your settings,
 * so both always list every type the server can send (docs/notifications.md has the full inventory).
 */
type NotifyKey = keyof PersonSettings["notify"];
export type Tone = NonNullable<PersonSettings["tone"]>;

export const NOTIFY_TYPES: readonly (readonly [NotifyKey, string, string])[] = [
  ["morning", "A short morning summary", "how the night went, anything from overnight, and a one-tap check-in"],
  ["away_precool", "Pre-cool is due and I'm not home", "with buttons: wait an hour, off tonight, start anyway"],
  ["empty_side", "My side is pre-cooled but empty", "in the evening; after midnight it waits for the morning"],
  ["left_on", "I turned my side on and left it empty", "after 20 min with nobody in it, with a Turn it off button"],
  ["not_started", "Ask to start my side if the bed isn't sure it's me", "after about 40 min lying down, with a Start now button"],
  ["pet_question", "Was the pet on the bed?", "daytime only; a question from the night comes with the morning summary"],
  ["problems", "Bed or sensor trouble", "the bed can't be reached or a sensor stops updating"],
];
const QUIET: readonly [NotifyKey, string, string] = ["quiet_hours", "Quiet while I sleep",
  "nothing between bedtime and wake; anything that comes up waits for the morning summary"];

export const notifyOnCount = (s: PersonSettings) => NOTIFY_TYPES.filter(([k]) => s.notify?.[k] ?? true).length;

/** One switch per type, plus quiet hours. `upd` saves a settings patch. */
export function NotifyToggles({ s, upd }: { s: PersonSettings; upd: (p: Partial<PersonSettings>) => void }) {
  return (
    <>
      {[...NOTIFY_TYPES, QUIET].map(([k, label, sub]) => (
        <div className="row" key={k}><span>{label}<br /><small className="mute">{sub}</small></span>
          <input type="checkbox" className="toggle" aria-label={label} checked={s.notify?.[k] ?? true}
            onChange={e => upd({ notify: { ...s.notify, [k]: e.target.checked } })} /></div>
      ))}
    </>
  );
}

const TONES: readonly (readonly [Tone, string, string])[] = [
  ["calm", "Calm", "Plain and quiet"], ["friendly", "Friendly", "Warmer, a little chattier"],
  ["cheeky", "Cheeky", "Playful teasing; alerts stay plain"],
];

/** Calm / Friendly / Cheeky, with a few sample lines in the chosen tone. */
export function TonePicker({ s, name, upd }: { s: PersonSettings; name: string; upd: (p: Partial<PersonSettings>) => void }) {
  const tone: Tone = s.tone ?? "calm";
  const [samples, setSamples] = useState<Record<Tone, string[]> | null>(null);
  useEffect(() => {
    let live = true;
    api.get<Record<Tone, string[]>>(`/api/voice/samples?name=${encodeURIComponent(name)}`).then(r => live && setSamples(r)).catch(() => {});
    return () => { live = false; };
  }, [name]);
  return (
    <div className="item" style={{ display: "grid", gap: 8 }}>
      <span>How it talks to you<br /><small className="mute">Notifications, suggestions and the night story. Same meaning and numbers in every tone.</small></span>
      <div className="seg" role="group" aria-label="How it talks to you">
        {TONES.map(([k, l]) => <button key={k} type="button" aria-pressed={tone === k} onClick={() => upd({ tone: k })}>{l}</button>)}
      </div>
      <span className="mute" style={{ fontSize: 12.5 }}>{TONES.find(t => t[0] === tone)?.[2]}. For example:</span>
      <ul className="mute" style={{ margin: 0, paddingLeft: 18, fontSize: 13, display: "grid", gap: 4 }} aria-live="polite">
        {(samples?.[tone] ?? []).slice(0, 3).map(x => <li key={x}>{x}</li>)}
        {!samples && <li>…</li>}
      </ul>
    </div>
  );
}
