import { cloneElement, isValidElement, type ReactElement, type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

/** A mouse or trackpad that can hover (Studio on a computer). Touch screens get the tap-to-open note instead. */
const canHover = () => typeof window !== "undefined" && !!window.matchMedia?.("(hover: hover) and (pointer: fine)").matches;

type Mode = "pop" | "note" | null;

/**
 * A short explanation for a word that needs one, an icon-only button or a status chip.
 *
 * - Hover or keyboard focus shows a small popover beside it (Esc or moving away closes it).
 * - On a touch screen the ⓘ opens the note inline, right under the line it belongs to; tap again or Esc to close.
 * - With `children`, that element (a chip, an icon button) is the trigger instead of an ⓘ, and shows the popover.
 *
 * The text is always linked with aria-describedby, so screen readers hear it whether it's showing or not.
 * The popover is fixed-position and kept inside the screen (8 px from each edge), so it never overflows at 360 px.
 */
export default function Tip({ text, label, children }: { text: ReactNode; label?: string; children?: ReactElement }) {
  const id = useId();
  const [mode, setMode] = useState<Mode>(null);
  const [pos, setPos] = useState<{ left: number; top: number; width: number } | null>(null);
  const trig = useRef<HTMLElement | null>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const hoverTimer = useRef<number>();

  // place the popover: centred under the trigger, nudged inside the screen, above it when there's no room below
  useLayoutEffect(() => {
    if (mode !== "pop" || !trig.current) { setPos(null); return; }
    const r = trig.current.getBoundingClientRect();
    const vw = document.documentElement.clientWidth || window.innerWidth, vh = window.innerHeight;   // clientWidth: not under a scrollbar
    const width = Math.min(280, vw - 16);
    const left = Math.min(Math.max(8, r.left + r.width / 2 - width / 2), vw - width - 8);
    const h = pop.current?.offsetHeight ?? 60;
    const below = r.bottom + 6;
    const top = below + h > vh - 8 && r.top - 6 - h > 8 ? r.top - 6 - h : below;
    setPos({ left, top, width });
  }, [mode, text]);

  // Esc closes (and doesn't also close the sheet or go back); scrolling or resizing closes a popover
  useEffect(() => {
    if (!mode) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); setMode(null); } };
    const away = () => setMode(m => (m === "pop" ? null : m));
    document.addEventListener("keydown", key, true);
    window.addEventListener("scroll", away, true);
    window.addEventListener("resize", away);
    return () => { document.removeEventListener("keydown", key, true); window.removeEventListener("scroll", away, true); window.removeEventListener("resize", away); };
  }, [mode]);
  useEffect(() => () => window.clearTimeout(hoverTimer.current), []);

  const enter = () => { if (!canHover()) return; window.clearTimeout(hoverTimer.current); hoverTimer.current = window.setTimeout(() => setMode(m => m ?? "pop"), 250); };
  const leave = () => { window.clearTimeout(hoverTimer.current); setMode(m => (m === "pop" ? null : m)); };
  const focus = (e: React.FocusEvent<HTMLElement>) => { let kb = true; try { kb = e.currentTarget.matches(":focus-visible"); } catch { /* old WebView */ } if (kb) setMode(m => m ?? "pop"); };
  const blur = () => setMode(m => (m === "pop" ? null : m));

  // a click in the note must not reach a surrounding <label> (it would flip that row's switch)
  const body = <span id={id} ref={pop} role="tooltip" hidden={!mode} onClick={e => { e.preventDefault(); e.stopPropagation(); }}
    className={mode === "note" ? "tip-note" : "tip-pop"}
    style={mode === "pop" ? (pos ? { left: pos.left, top: pos.top, width: pos.width } : { left: 8, top: 0, visibility: "hidden", width: Math.min(280, (document.documentElement.clientWidth || window.innerWidth) - 16) }) : undefined}>
    {text}
  </span>;

  if (children && isValidElement(children)) {
    const props = children.props as { tabIndex?: number; onClick?: (e: React.MouseEvent) => void };
    const interactive = typeof children.type === "string" && ["button", "a", "input", "select"].includes(children.type);
    const trigger = cloneElement(children as ReactElement<Record<string, unknown>>, {
      "aria-describedby": id,
      ref: (el: HTMLElement | null) => { trig.current = el; },
      tabIndex: interactive ? props.tabIndex : props.tabIndex ?? 0,
      onClick: (e: React.MouseEvent) => { props.onClick?.(e); if (!interactive) setMode(m => (m ? null : "pop")); },
      onMouseEnter: enter, onMouseLeave: leave, onFocus: focus, onBlur: blur,
    });
    return <>{trigger}{body}</>;
  }

  return <span className="tip">
    <button type="button" className="tip-btn" aria-label={label ?? "What does this mean?"} aria-describedby={id} aria-expanded={!!mode}
      ref={el => { trig.current = el; }}
      onClick={e => { e.preventDefault(); e.stopPropagation(); if (canHover()) setMode("pop"); else setMode(m => (m ? null : "note")); }}
      onMouseEnter={enter} onMouseLeave={leave} onFocus={focus} onBlur={blur}>
      <span aria-hidden="true">i</span>
    </button>
    {body}
  </span>;
}
