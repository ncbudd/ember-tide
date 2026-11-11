import { useLocation } from "react-router-dom";
import { createContext, type ReactNode, useContext, useEffect, useId, useRef, useState } from "react";

/** Open/closed per section, remembered on this device. */
const KEY = "et.folds";
const read = (): Record<string, boolean> => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
const write = (id: string, open: boolean) => {
  try { localStorage.setItem(KEY, JSON.stringify({ ...read(), [id]: open })); } catch { /* private window: just not remembered */ }
};

/** Inside this, every Fold still opens and closes but the controls in its body are disabled (a view-only account). */
export const FoldLock = createContext(false);

/**
 * A collapsible settings section: a big tappable header (name, a one-line summary of where it stands, chevron) and its
 * contents. Used on the phone (Settings, Rhythm, Sleep) and in Studio (`className="box"`). `attention` opens it by itself (a permission was refused, a sync failed), whatever was remembered.
 */
export default function Fold({ id, anchor, title, summary, defaultOpen = false, attention = false, reveal = false, className, keepSummary = false, children }: {
  id: string;
  /** a link like /settings#watch (from a notification) opens and scrolls to the section with this anchor */
  anchor?: string; title: string; summary?: ReactNode; defaultOpen?: boolean; attention?: boolean;
  /** opens it (once each time this turns true), e.g. when an Edit elsewhere targets something inside */
  reveal?: boolean;
  /** keep the summary in the header while open (Studio cards whose summary is live state, e.g. "3 devices · all reporting") */
  keepSummary?: boolean;
  /** "box" in Studio (a card with the header inside), "fold-sub" for a small fold inside a card */
  className?: string; children: ReactNode;
}) {
  const hash = useLocation().hash;                       // follows a link opened while this page is already showing
  const linked = !!anchor && hash === `#${anchor}`;
  const [open, setOpen] = useState<boolean>(() => linked || (read()[id] ?? defaultOpen));
  const sect = useRef<HTMLElement>(null);
  useEffect(() => { if (linked) { setOpen(true); sect.current?.scrollIntoView?.({ block: "start" }); } }, [linked]);
  const body = useId();
  const locked = useContext(FoldLock);
  useEffect(() => { if (attention) setOpen(true); }, [attention]);
  useEffect(() => { if (reveal) setOpen(true); }, [reveal]);
  const toggle = () => setOpen(o => { write(id, !o); return !o; });
  return <section ref={sect} className={`fold${open ? " open" : ""}${className ? ` ${className}` : ""}`}>
    <button type="button" className="fold-head" aria-expanded={open} aria-controls={body} onClick={toggle}>
      <span className="k">{title}</span>
      {summary && (!open || keepSummary) && <span className={`fold-sum${attention ? " warn" : ""}`}>{summary}</span>}
      <span className="fold-chev" aria-hidden="true">›</span>
    </button>
    <div id={body} className="fold-body" hidden={!open}>{locked ? <fieldset disabled className="fold-lock">{children}</fieldset> : children}</div>
  </section>;
}
