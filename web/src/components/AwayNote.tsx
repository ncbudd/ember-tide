import { useApp } from "../ctx";
import type { PersonView } from "../types";
import Tip from "./Tip";

/**
 * The away pieces shared by Tonight and the Simple app:
 * - the morning question ("Were you away Tuesday night?") when the bed saw nobody on your side, or asking if you're back;
 * - the "You're away" note with its "I'm back" button.
 * Each renders nothing when it doesn't apply. `tz` is the server's time zone, so dates match the clock on the bed.
 */
export default function AwayNote({ person, me, isOwner, tz }: { person: PersonView; me: PersonView; isOwner: boolean; tz: string }) {
  const { act } = useApp();
  const mine = person.id === me.id;
  const n = person.night;
  const base = `/api/people/${person.id}`;
  const q = mine ? n.away_check : null;
  const day = q ? new Date(q.night + "T12:00").toLocaleDateString(undefined, { weekday: "long" }) : "";
  const ans = (a: string) => { if (q) void act(`${base}/away-check`, { night: q.night, answer: a }); };
  const until = n.away?.until ? new Date(n.away.until * 1000).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: tz }) : "";
  return <>
    {q && <div className="planrow note away-note">
      {q.kind === "away"
        ? <>Were you away {day} night? Your side looked empty.
          <Tip label="Why are you asking?" text={<>{q.why}. If you were away, that night won't be used when Autopilot works out your best temperatures.</>} /></>
        : <>Welcome back? You're marked away, but it looks like you slept in your side {day} night.
          <Tip label="Why are you asking?" text={<>{q.why}.</>} /></>}
      <span className="chips" style={{ marginTop: 8 }}>{q.kind === "away"
        ? <><button className="chip" onClick={() => ans("away")}>Away a few days</button><button className="chip" onClick={() => ans("night")}>Just that night</button><button className="chip" onClick={() => ans("home")}>I was home</button></>
        : <><button className="chip" onClick={() => ans("back")}>Yes, I'm back</button><button className="chip" onClick={() => ans("still")}>Still away</button></>}</span>
    </div>}
    {n.away && <div className="planrow note away-note">{mine ? "You're" : `${person.name} is`} away{until ? ` until ${until}` : ""}. {mine ? "Your" : "Their"} side stays off and Autopilot's learning waits. Using the side (or the dial) means back.
      {(mine || isOwner) && <> <button className="pill" onClick={() => void act(`${base}/away`, { away: false })}>{mine ? "I'm back" : `${person.name}'s back`}</button></>}</div>}
  </>;
}
