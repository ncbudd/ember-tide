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
  ["away_precool", "Pre-conditioning is due and I'm not home", "with buttons: wait an hour, off tonight, start anyway"],
  ["empty_side", "My side is pre-conditioned but empty", "in the evening; after midnight it waits for the morning"],
  ["left_on", "I turned my side on and left it empty", "after 20 min with nobody in it, with a Turn it off button"],
  ["not_started", "Ask to start my side if the bed isn't sure it's me", "after about 40 min lying down, with a Start now button"],
  ["pet_question", "Was the pet on the bed?", "daytime only; a question from the night comes with the morning summary"],
  ["problems", "Bed or sensor trouble", "the bed can't be reached or a sensor stops updating"],
  ["hub", "Bed hub needs a look", "a fault, or the water needs checking (owner only); once a day per problem"],
  ["account", "A connected account needs signing in again", "Oura, Withings and similar; daytime only, at most once a day, with where to fix it"],
  ["reach", "A phone stopped getting notifications", "owner only; tells you when someone's notifications no longer arrive; daytime only"],
  ["bridge", "The Home Assistant link is down", "owner only; after a few hours without a connection; daytime only"],
  ["storage", "The server is nearly out of space", "owner only; daytime only, at most once a day"],
  ["wearable", "My watch or ring has gone quiet", "after two nights in bed with nothing from it; daytime only, at most once a day, with where to fix it"],
  ["bed_silent", "The bed's sensors reported nothing all night", "told with the morning, once a night, when the bed had to run on your usual times"],
  ["sensor", "A presence sensor keeps flipping or looks stuck", "owner only; daytime only, once a day per sensor (a stuck one once a week); opens Studio, Devices"],
  ["target", "My side isn't reaching its temperature", "told with the morning; if a side is far off for over an hour, owners hear at once"],
  ["maintenance", "Care reminders", "a job like purification tablets is due; daytime only, at most one a day, with It's done and Remind me next week buttons"],
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
