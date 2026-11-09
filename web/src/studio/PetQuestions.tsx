import { useEffect, useState } from "react";
import { api } from "../api";
import PetQuestion from "../components/PetQuestion";
import { clock } from "../format";

interface QRow { id: string; person: string; title: string; status: string; answer: string | null; kind: string; start: number; spans: { evidence: string | null }[] }
interface PetSig { pet: string; name: string; learned: boolean; minutes: number; hr: number | null; br: number | null }

const ANSWER: Record<string, string> = { yes: "Yes, the pet", me: "No, it was them", unsure: "Not sure" };
const EVIDENCE: Record<string, string> = { watch: "watch didn't match", range: "outside their usual", signature: "like the pet", scale: "scale", heart: "fast heart" };

/** Studio: the pet questions (answer the open one here too) and what's been learned about each pet. */
export default function PetQuestions({ person, tz }: { person: string; tz: string }) {
  const [d, setD] = useState<{ questions: QRow[]; pets: PetSig[] } | null>(null);
  const load = () => api.get<{ questions: QRow[]; pets: PetSig[] }>("/api/pet-questions").then(setD).catch(() => setD(null));
  useEffect(() => { void load(); }, []);
  if (!d || !d.pets.length) return null;
  const mine = d.questions.filter(q => q.person === person).slice(0, 6);
  return (
    <div className="box"><span className="k">Pets: questions and what's learned</span>
      <PetQuestion key={person} person={person} onAnswered={load} notifications={false} />
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {d.pets.map(p => <span key={p.pet} className="pill">{p.name}: {p.learned ? `learned from ${p.minutes} min · about ${p.hr} bpm, ${p.br} breaths` : "not learned yet"}</span>)}
      </div>
      {mine.length > 0 && <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 13 }}>
        {mine.map(q => <li key={q.id}>{clock(q.start, tz)} · {q.title.replace(/^Was /, "").replace(/\?$/, "")} · {q.status === "answered" ? ANSWER[q.answer ?? ""] ?? q.answer : q.status === "open" ? "waiting for an answer" : q.status}
          {q.spans[0]?.evidence ? <span className="mute"> ({EVIDENCE[q.spans[0].evidence] ?? q.spans[0].evidence})</span> : null}</li>)}
      </ul>}
    </div>
  );
}
