import { nightsText } from "../format";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** Rhythm days (the evening a night starts on) for the nights before a work day / a day off. Mirrors models.py. */
const workNights = (wd: number[]) => [...new Set(wd.map(d => (d + 6) % 7))].sort();
const offNights = (wd: number[]) => [0, 1, 2, 3, 4, 5, 6].filter(d => !workNights(wd).includes(d));

/** "Nights before a work day" / "Nights before a day off" for a rhythm that follows work days, else its own days. */
export function rhythmNights(r: { days: number[]; nights?: string }): string {
  if (r.nights === "work") return `Nights before a work day · ${nightsText(r.days)}`;
  if (r.nights === "off") return r.days.length ? `Nights before a day off · ${nightsText(r.days)}` : "Nights before a day off · none";
  return nightsText(r.days);
}

export function workDaysText(wd: number[]): string {
  if (!wd.length) return "No work days";
  if (wd.length === 7) return "Every day";
  if (wd.join() === "0,1,2,3,4") return "Monday to Friday";
  if (wd.join() === "5,6") return "Weekends";
  return wd.map(d => DAYS[d]).join(", ");
}

/** Seven big day buttons. Work days are the days you get up for work (or school, or anything with a fixed start). */
export default function WorkDays({ value, onChange }: { value: number[]; onChange: (v: number[]) => void }) {
  const flip = (d: number) => onChange(value.includes(d) ? value.filter(x => x !== d) : [...value, d].sort());
  return (
    <div className="wd" role="group" aria-label="Work days">
      <div className="wd-days">
        {DAYS.map((d, i) => (
          <button key={d} type="button" className="wd-day" aria-pressed={value.includes(i)} aria-label={LONG[i]} onClick={() => flip(i)}>
            <span>{d}</span>
          </button>
        ))}
      </div>
      <div className="wd-quick">
        <button type="button" className="chip" aria-pressed={value.join() === "0,1,2,3,4"} onClick={() => onChange([0, 1, 2, 3, 4])}>Mon–Fri</button>
        <button type="button" className="chip" aria-pressed={value.join() === "5,6"} onClick={() => onChange([5, 6])}>Weekends</button>
        <button type="button" className="chip" aria-pressed={value.length === 0} onClick={() => onChange([])}>No fixed work days</button>
      </div>
      <p className="mute wd-sum">
        <span><b>Nights before a work day:</b> {workNights(value).length ? nightsText(workNights(value)) : "none"}</span>
        <span><b>Nights before a day off:</b> {offNights(value).length ? nightsText(offNights(value)) : "none"}</span>
      </p>
    </div>
  );
}
