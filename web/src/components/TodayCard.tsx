import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { allow, fitGuard } from "./fitGuard";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { clock, contextText } from "../format";
import type { PersonView, Snapshot, Suggestions } from "../types";

/** A short "Today" note for the empty middle of Tonight in the daytime: how last night went, one autopilot suggestion or
 *  observation if there is one, Autopilot's learning progress, and when tonight starts. Hides when there's nothing
 *  to say. Tapping it opens Sleep. */

interface NightLite { date: string; has_data: boolean; in_progress: boolean; in_bed_min?: number; time_to_settle_min?: number | null; feedback: { temp?: string } | null }

function dur(min: number): string {
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return h ? (m ? `${h} h ${m} min` : `${h} h`) : `${m} min`;
}

const FELT: Record<string, string> = { warm: "felt warm", cold: "felt cool", right: "felt just right", just_right: "felt just right" };

export default function TodayCard({ me, snap }: { me: PersonView; snap: Snapshot }) {
  const nav = useNavigate();
  const [night, setNight] = useState<NightLite | null>(null);
  const [sug, setSug] = useState<Suggestions | null>(null);
  const hour = Math.floor(snap.now / 3600);
  // only as much as there's room for: the full note, a one-line note, or nothing (on short screens the controls matter more)
  const ref = useRef<HTMLButtonElement>(null);
  const heights = useRef<Record<number, number>>({ 2: 84, 1: 44 });
  const [level, setLevel] = useState(2);
  // once it had to shrink to fit, it doesn't grow back until the screen size changes (otherwise shrink → fits →
  // grow → overflow loops forever and React gives up)
  const shrunkAt = useRef<string | null>(null);
  const guard = useRef(fitGuard());
  useLayoutEffect(() => {
    const slot = document.querySelector(".tn-lower .turnon-slot") as HTMLElement | null;
    const scr = slot?.closest(".screen") as HTMLElement | null;
    if (!scr || !slot) return;
    const check = () => {
      if (ref.current) heights.current[level] = ref.current.offsetHeight;
      const size = `${scr.clientWidth}x${scr.clientHeight}`;
      if (scr.scrollHeight > scr.clientHeight + 1 && level > 0) { shrunkAt.current = size; if (allow(guard.current)) setLevel(level - 1); return; }
      if (level < 2 && shrunkAt.current !== size) {
        const used = Array.from(slot.children).reduce((t, c) => t + (c as HTMLElement).offsetHeight, 0) + (slot.children.length - 1) * 12;
        const extra = (heights.current[level + 1] ?? 84) - (level ? heights.current[level] : -12);
        if (slot.clientHeight - used >= extra && allow(guard.current)) setLevel(level + 1);
      }
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(scr);
    ro.observe(slot);
    return () => ro.disconnect();
  });
  useEffect(() => {
    let live = true;
    api.get<NightLite[]>(`/api/people/${me.id}/nights?count=2`)
      .then(ns => live && setNight(ns.find(n => n.has_data && !n.in_progress && (n.in_bed_min ?? 0) >= 30) ?? null)).catch(() => {});
    api.get<Suggestions>(`/api/people/${me.id}/autopilot/suggestions`).then(s => live && setSug(s)).catch(() => {});
    return () => { live = false; };
  }, [me.id, hour]);

  const lines: { text: string; strong?: boolean }[] = [];
  if (night) {
    const bits = [`Last night · ${dur(night.in_bed_min ?? 0)} in bed`];
    if (night.time_to_settle_min != null) bits.push(`settled in ${dur(night.time_to_settle_min)}`);
    const f = night.feedback?.temp && FELT[night.feedback.temp];
    if (f) bits.push(f);
    lines.push({ text: bits.join(" · ") });
  }
  const item = sug?.items?.[0];
  if (item) lines.push({ text: item.title, strong: true });
  const lr = sug?.learning;
  if (lr && !lr.done && me.settings.autopilot.mode !== "off") lines.push({ text: lr.paused ? `Autopilot's learning is paused while you're away (${lr.have} of ${lr.need} nights)` : `Autopilot is learning your nights: ${lr.have} of ${lr.need}` });
  const ctx = contextText(me.night, snap.now, snap.tz);
  const sc = me.schedule;
  if (ctx) lines.push({ text: ctx });
  else if (sc && snap.now < sc.precool && !lines.length) lines.push({ text: `Tonight: pre-conditioning from ${clock(sc.precool, snap.tz)} · bedtime ${clock(sc.bedtime, snap.tz)}` });
  if (!lines.length || level === 0) return null;
  const one = lines.find(l => l.strong) ?? lines[0];          // the one line worth showing when there's little room
  if (level === 1) return (
    <button ref={ref} className="today today-one card" onClick={() => nav(night ? `/sleep?night=${night.date}` : "/sleep")} aria-label={`Today: ${one.text}. Open Sleep`}>
      <span className={one.strong ? "today-strong" : "today-line"}>{one.text}</span><span className="today-more" aria-hidden>›</span>
    </button>
  );
  return (
    <button ref={ref} className="today card" onClick={() => nav(night ? `/sleep?night=${night.date}` : "/sleep")} aria-label="Today. Open Sleep">
      <span className="today-head"><span className="k">Today</span><span className="today-more" aria-hidden>Sleep ›</span></span>
      {lines.slice(0, 3).map((l, i) => <span key={i} className={l.strong ? "today-strong" : "today-line"}>{l.text}</span>)}
    </button>
  );
}
