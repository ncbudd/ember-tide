import { useRef, useState } from "react";

/** Should a finished horizontal drag dismiss the card? A drag past 30% of the card's width, or a quick flick. */
export function swipeDismisses(dx: number, width: number, ms: number): boolean {
  const far = Math.abs(dx) >= width * 0.3;
  const flick = Math.abs(dx) >= 40 && Math.abs(dx) / Math.max(ms, 1) >= 0.5;
  return far || flick;
}

/** Swipe a card sideways (touch/pen only; mouse users have the button) to send it away. Vertical scrolling is never
 *  taken over: a drag only counts once it is clearly sideways. `onAway` runs after the slide-out. */
export function useSwipeAway(onAway: () => void) {
  const [dx, setDx] = useState(0);
  const [leaving, setLeaving] = useState(false);
  const drag = useRef<{ x: number; y: number; t: number; on: boolean; w: number } | null>(null);
  const still = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finish = (dir: number) => {
    if (still) { onAway(); return; }
    setLeaving(true); setDx(dir * 600);
    window.setTimeout(onAway, 220);
  };
  const handlers = {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
      if (e.pointerType === "mouse") return;
      drag.current = { x: e.clientX, y: e.clientY, t: e.timeStamp, on: false, w: e.currentTarget.offsetWidth };
    },
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => {
      const d = drag.current;
      if (!d) return;
      const mx = e.clientX - d.x, my = e.clientY - d.y;
      if (!d.on) {
        if (Math.abs(my) > 12 && Math.abs(my) > Math.abs(mx)) { drag.current = null; return; }
        if (Math.abs(mx) < 12 || Math.abs(mx) < Math.abs(my) * 1.5) return;
        d.on = true;
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* fine without */ }
      }
      setDx(mx);
    },
    onPointerUp: (e: React.PointerEvent<HTMLElement>) => {
      const d = drag.current;
      drag.current = null;
      if (!d?.on) return;
      if (swipeDismisses(dx, d.w, e.timeStamp - d.t)) finish(dx < 0 ? -1 : 1);
      else setDx(0);
    },
    onPointerCancel: () => { drag.current = null; setDx(0); },
  };
  const style: React.CSSProperties = {
    transform: dx ? `translateX(${dx}px)` : undefined,
    opacity: leaving ? 0 : dx ? Math.max(0.35, 1 - Math.abs(dx) / 500) : undefined,
    transition: drag.current?.on || still ? "none" : "transform .2s ease, opacity .2s ease",
  };
  return { handlers, style, reset: () => { setDx(0); setLeaving(false); } };
}
