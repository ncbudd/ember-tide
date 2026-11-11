import type { Learning } from "../types";

/** "Tue, Nov 4" for an evening date ("2026-11-04"), read as a calendar date (no time zone shift). */
function nightName(d: string): string {
  const [y, m, day] = d.split("-").map(Number);
  return new Date(y, m - 1, day).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

/** Recent nights that didn't count as learning nights, each with its reason (Autopilot.left_out on the server). */
export default function LeftOutNights({ learning, max = 4 }: { learning: Learning | null | undefined; max?: number }) {
  const left = learning?.left_out ?? [];
  if (!left.length) return null;
  return (
    <div className="mute" style={{ fontSize: 12.5, display: "grid", gap: 2 }}>
      <span>Nights that didn't count:</span>
      {left.slice(0, max).map(n => <span key={n.date}>{nightName(n.date)} · {n.why}</span>)}
    </div>
  );
}
