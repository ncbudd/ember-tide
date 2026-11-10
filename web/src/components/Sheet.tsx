import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { pushBack } from "./back";

/** Sheets that are open, oldest first: Esc only closes the one on top. */
const openSheets: symbol[] = [];

/**
 * Bottom sheet that closes the way it looks like it should: drag it down (or tap outside, or Esc).
 * A short drag snaps back; past ~90 px, or a quick flick, it closes. Taps on its buttons still work.
 * A sheet taller than the screen scrolls; then only the handle at the top drags it closed, so scrolling never does.
 * `form`: a form with edits (Fix this night): tapping outside doesn't close it and only the handle drags it, so a
 * stray tap or swipe can't throw the edits away. Cancel / Save close it.
 */
export default function Sheet({ onClose, children, locked, form, ...aria }: {
  onClose: () => void; children: ReactNode; locked?: boolean; form?: boolean;
  role?: string; "aria-label"?: string; "aria-labelledby"?: string; "aria-describedby"?: string;
}) {
  const [dy, setDy] = useState(0);
  const [leaving, setLeaving] = useState(false);
  const drag = useRef<{ y: number; t: number; moved: boolean } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const [tall, setTall] = useState(false);            // content taller than the sheet can show: it scrolls
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setTall(el.scrollHeight > el.clientHeight + 2);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    for (const c of Array.from(el.children)) ro.observe(c);
    return () => ro.disconnect();
  }, []);
  const handleOnly = tall || !!form;

  const close = () => {
    if (locked) return;
    setLeaving(true);
    window.setTimeout(onClose, 160);
  };
  const closeRef = useRef(close);
  closeRef.current = close;
  // Android back / Esc close it. A form asks first: the first press shows "press again to discard", a second one
  // within 4 s closes it, so an accidental edge swipe doesn't throw the edits away.
  const [warn, setWarn] = useState(false);
  const warnAt = useRef(0);
  const backPress = () => {
    if (!form || performance.now() - warnAt.current < 4000) { closeRef.current(); return; }
    warnAt.current = performance.now();
    setWarn(true);
    window.setTimeout(() => setWarn(false), 4000);
    unBack.current = pushBack(backPress);                      // still open: the next back press comes here again
  };
  const unBack = useRef<() => void>(() => {});
  useEffect(() => { unBack.current = pushBack(backPress); return () => unBack.current(); }, []);
  // focus moves into the sheet when it opens and goes back to what opened it when it closes
  const [needClose, setNeedClose] = useState(false);
  const me = useRef(Symbol("sheet"));
  useEffect(() => {
    const id = me.current;
    openSheets.push(id);
    const opener = document.activeElement as HTMLElement | null;
    const el = ref.current;
    el?.focus({ preventScroll: true });
    // a sheet with no Cancel or Close button of its own still needs one a screen reader can reach
    if (el) setNeedClose(!Array.from(el.querySelectorAll("button")).some(b => /^\s*(cancel|close)/i.test(b.textContent ?? "")));
    return () => {
      const i = openSheets.indexOf(id);
      if (i >= 0) openSheets.splice(i, 1);
      if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape" && openSheets[openSheets.length - 1] === me.current) backPress(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  const down = (e: React.PointerEvent) => {
    if (locked || (ref.current && ref.current.scrollTop > 0)) return;
    if (handleOnly && !(e.target as HTMLElement).closest?.(".sheet-handle")) return;
    drag.current = { y: e.clientY, t: performance.now(), moved: false };
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const off = Math.max(0, e.clientY - d.y);
    if (!d.moved && off > 8) { d.moved = true; ref.current?.setPointerCapture(e.pointerId); }
    if (d.moved) setDy(off);
  };
  const up = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (!d || !d.moved) return;
    const off = Math.max(0, e.clientY - d.y);
    const speed = off / Math.max(1, performance.now() - d.t);       // px per ms
    // a drag isn't a tap: swallow the click that would land on whatever button the finger lifted over
    const eat = (ev: MouseEvent) => { ev.stopPropagation(); ev.preventDefault(); };
    window.addEventListener("click", eat, { capture: true, once: true });
    window.setTimeout(() => window.removeEventListener("click", eat, { capture: true }), 250);   // only that one click, never a later tap
    if (off > 90 || (off > 30 && speed > 0.6)) close(); else setDy(0);
  };

  return <>
    <div className="scrim" style={{ opacity: leaving ? 0 : Math.max(0.2, 1 - dy / 300) }} onClick={form ? undefined : close} />
    <div ref={ref} className={`sheet${handleOnly ? " sheet-scroll" : ""}`} tabIndex={-1} {...aria}
      style={{ outline: "none",  transform: `translateY(${leaving ? "100%" : `${dy}px`})`, transition: drag.current?.moved ? "none" : "transform .16s ease-out", touchAction: handleOnly ? "pan-y" : "pan-x" }}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { drag.current = null; setDy(0); }}>
      {handleOnly && <div className="sheet-handle" aria-hidden="true" />}
      {warn && <div role="status" className="mute" style={{ fontSize: 13, textAlign: "center" }}>Press back again to close without saving</div>}
      {children}
      {needClose && <button type="button" className="sr-close" onClick={backPress}>Close</button>}
    </div>
  </>;
}
