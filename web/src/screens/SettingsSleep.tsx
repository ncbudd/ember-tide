import Fold from "../components/Fold";
import type { PersonSettings, TrustMore } from "../types";

const TRUST: { key: TrustMore; label: string; why: string }[] = [
  { key: "wearable", label: "Watch or ring", why: "Recommended. Most watches and rings measure sleep better than a strip in the bed." },
  { key: "balanced", label: "Balanced", why: "Both count the same." },
  { key: "bed", label: "The bed", why: "If your watch is often loose or off at night." },
];

/** "Which to trust more" as three big buttons (Settings and first-time setup). */
export function TrustPicker({ value, onChange }: { value: TrustMore; onChange: (v: TrustMore) => void }) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div className="seg" role="group" aria-label="Which to trust more">
        {TRUST.map(t => <button key={t.key} aria-pressed={value === t.key} onClick={() => onChange(t.key)}>{t.label}</button>)}
      </div>
      <span className="mute" style={{ fontSize: 12.5 }}>{TRUST.find(t => t.key === value)?.why}</span>
    </div>
  );
}

const IN_BED = [0, 1, 3, 5, 10, 15];
const UP = [1, 3, 5, 10, 15];

export const sleepDataSummary = (s: PersonSettings) =>
  `Trusting ${TRUST.find(t => t.key === (s.autopilot.trust_more ?? "wearable"))?.label.toLowerCase()}${s.in_bed_after_min != null || s.up_after_min != null ? " · tuned" : ""}`;

/** Watch or ring vs the bed, pet questions, and how quickly "in bed" / "got up" count. Phone Settings fold. */
export default function SettingsSleep({ s, upd }: { s: PersonSettings; upd: (p: Partial<PersonSettings>) => void }) {
  return (
    <Fold id="settings.sleepdata" title="Sleep data" summary={sleepDataSummary(s)}>
      <div className="card list"><SleepDataRows s={s} upd={upd} /></div>
    </Fold>
  );
}

/** The rows themselves (also in Studio → Your settings). */
export function SleepDataRows({ s, upd }: { s: PersonSettings; upd: (p: Partial<PersonSettings>) => void }) {
  const trust = s.autopilot.trust_more ?? "wearable";
  const tuned = s.in_bed_after_min != null || s.up_after_min != null;
  const inBed = s.in_bed_after_min ?? 3;
  const up = s.up_after_min ?? 3;
  return (
      <>
        <div className="item" style={{ display: "grid", gap: 8, padding: "11px 0" }}>
          <span>When the bed and your watch or ring disagree, trust</span>
          <TrustPicker value={trust} onChange={v => upd({ autopilot: { ...s.autopilot, trust_more: v } })} />
          <span className="faint" style={{ fontSize: 12 }}>Used for your in-bed and asleep times, sleep stages, heart rate, your normal range and whether it's you in bed.</span>
        </div>
        <div className="row"><span>Wake with my phone's alarm<br /><span className="mute" style={{ fontSize: 12 }}>The warm wake lines up with your next alarm: earlier, or a little later if you set a second one to sleep in. Needs "next alarm" shared from your phone.</span></span>
          <input type="checkbox" className="toggle" aria-label="Wake with my phone's alarm" checked={s.wake_with_alarm ?? true} onChange={e => upd({ wake_with_alarm: e.target.checked })} /></div>
        <div className="row"><span>Ask me about the pet<br /><span className="mute" style={{ fontSize: 12 }}>After something lay on your side that didn't look like you: "Was it the dog?" One question a night at most.</span></span>
          <input type="checkbox" className="toggle" aria-label="Ask me about the pet" checked={s.pet_questions ?? true} onChange={e => upd({ pet_questions: e.target.checked })} /></div>
        {(s.pet_questions ?? true) && <div className="row"><span>Also as a notification<br /><span className="mute" style={{ fontSize: 12 }}>Only between 8 AM and 9 PM.</span></span>
          <input type="checkbox" className="toggle" aria-label="Pet questions as a notification" checked={s.notify?.pet_question ?? true} onChange={e => upd({ notify: { ...s.notify, pet_question: e.target.checked } })} /></div>}
        <div className="row"><span>Lying down before the night starts<br /><span className="mute" style={{ fontSize: 12 }}>Recommended: 3 min. Used by all your rhythms.</span></span>
          <select aria-label="Minutes lying down before the night starts" value={inBed} onChange={e => upd({ in_bed_after_min: Number(e.target.value) })}>
            {IN_BED.map(m => <option key={m} value={m}>{m === 0 ? "At once" : `${m} min`}</option>)}
          </select></div>
        <div className="row"><span>Out of bed before it counts as up<br /><span className="mute" style={{ fontSize: 12 }}>Recommended: 3 min. Shorter trips, like the bathroom, stay out of your night's story.</span></span>
          <select aria-label="Minutes out of bed before it counts as getting up" value={up} onChange={e => upd({ up_after_min: Number(e.target.value) })}>
            {UP.map(m => <option key={m} value={m}>{m} min</option>)}
          </select></div>
        {tuned && <div className="row"><span className="mute" style={{ fontSize: 13 }}>Changed from the recommended times</span>
          <button className="btn" onClick={() => upd({ in_bed_after_min: null, up_after_min: null })}>Back to recommended</button></div>}
      </>
  );
}
