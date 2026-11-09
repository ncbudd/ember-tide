import { type CSSProperties, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { personaColor } from "../format";
import type { PersonView } from "../types";
import Tip from "./Tip";

/**
 * The one way to pick a side of the bed: a drawing of the bed from above, headboard and pillows at the top, and you
 * tap the half that's yours. Used by first-time setup, "who's using this phone", Settings, Studio → People & access,
 * guest mode and the device add flow (BedZones draws its zone map with it too).
 *
 * Left and right are always as you lie in bed (the way the bed itself names its sides). Seen from above with the
 * headboard at the top, that's mirrored: you look at the bed from its foot, so the side that's on your left as you
 * lie is on the right of the drawing. The labels say it, so nobody has to work it out.
 */

export type Side = "left" | "right";
export interface Half { name?: string; color?: string; lines?: ReactNode }

const otherSide = (z: Side): Side => (z === "left" ? "right" : "left");

/** Which way the bed names its sides. Most name them as you lie; some (found 2026-11-08) from the foot of the bed,
 * so the bed's "left" zone is the right side as you lie. Zones are always the bed's names; only labels follow this.
 * Loaded once from /api/bed-orientation and shared by every picker on the page. */
// the last answer is remembered on this device, so starting offline (or while the server restarts) still labels right
let fromFoot = (() => { try { return localStorage.getItem("et.bedFromFoot") === "1"; } catch { return false; } })();
const subs = new Set<() => void>();
let loaded = false;
function setBedFromFoot(v: boolean) {
  fromFoot = v;
  try { localStorage.setItem("et.bedFromFoot", v ? "1" : "0"); } catch { /* private window */ }
  subs.forEach(f => f());
}
function loadOrientation() {
  if (loaded) return;
  loaded = true;
  api.get<{ from_foot: boolean }>("/api/bed-orientation").then(r => setBedFromFoot(!!r.from_foot))
    .catch(() => { loaded = false; });                     // try again next time the app comes back to the front
}
if (typeof document !== "undefined") document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") loadOrientation(); });
export function useBedFromFoot(): boolean {
  const [, tick] = useState(0);
  useEffect(() => {
    const f = () => tick(x => x + 1);
    subs.add(f);
    loadOrientation();
    return () => { subs.delete(f); };
  }, []);
  return fromFoot;
}
/** The side as you lie for one of the bed's zones. */
export const lieSide = (zone: Side): Side => (fromFoot ? otherSide(zone) : zone);
/** Screen order, seen from the foot of the bed: the right side as you lie is on the left of the drawing. */
const screenZones = (): Side[] => ["right", "left"].map(l => (fromFoot ? otherSide(l as Side) : l)) as Side[];
export const sideTitle = (zone: Side) => (lieSide(zone) === "left" ? "Left side" : "Right side");
/** "left side" / "right side" as you lie, for the middle of a sentence. */
export const sideLower = (zone: string) => (lieSide(zone === "right" ? "right" : "left") === "left" ? "left side" : "right side");
const sideAria = (zone: Side) => `${sideTitle(zone)} as you lie in bed`;
const CAPTION = "As you lie in bed, which side is yours? Tap it.";

/** What each half shows when a person picks their side: theirs where they tapped, the partner's on the other half. */
export function personHalves(value: Side | null, me: { name: string; color: string }, partner?: { name: string; color: string } | null): Partial<Record<Side, Half>> {
  if (!value) return {};
  return { [value]: me, ...(partner ? { [otherSide(value)]: partner } : {}) };
}
/** Each half shows whoever sleeps there now. */
export function peopleHalves(people: PersonView[]): Partial<Record<Side, Half>> {
  const out: Partial<Record<Side, Half>> = {};
  for (const p of people) if (p.zone === "left" || p.zone === "right") out[p.zone] = { name: p.name, color: personaColor(p) };
  return out;
}

export default function BedSidePicker({ value, onChange, halves = {}, whole, caption, label = "Your side of the bed", disabled, foot = true, className = "" }: {
  value: Side | "both" | null;
  onChange?: (v: Side | "both") => void;
  halves?: Partial<Record<Side, Half>>;
  /** the label of a "whole bed" option (guest mode); none when left out */
  whole?: string;
  /** null hides it */
  caption?: ReactNode | null;
  label?: string;
  disabled?: boolean;
  foot?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useBedFromFoot();
  const SCREEN = screenZones();
  const pick = (v: Side | "both") => { if (!disabled) onChange?.(v); };
  // arrow keys move between the halves (and the whole-bed option) like any radio group
  const order: (Side | "both")[] = whole ? [...SCREEN, "both"] : SCREEN;
  const onKey = (e: KeyboardEvent) => {
    if (!onChange) return;
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    e.stopPropagation();
    const i = Math.max(0, order.indexOf(value ?? SCREEN[0]));
    const next = order[(i + step + order.length) % order.length];
    pick(next);
    window.requestAnimationFrame(() => ref.current?.querySelector<HTMLElement>(`[data-side="${next}"]`)?.focus());
  };
  const tabbable = (v: Side | "both") => (value ? value === v : v === SCREEN[0]) ? 0 : -1;
  return <div className={`bedpick ${className}`} ref={ref}>
    {caption !== null && <p className="bedpick-cap">{caption ?? CAPTION}</p>}
    <div className="bedpick-bed" role={onChange ? "radiogroup" : "group"} aria-label={label} onKeyDown={onKey}>
      <div className="bedpick-head" aria-hidden><span>Headboard</span></div>
      <div className="bedpick-halves">
        {SCREEN.map(z => {
          const h = halves[z];
          const on = value === z || value === "both";
          const style = h?.color ? { "--c": h.color } as CSSProperties : undefined;
          const body = <>
            <span className="bedpick-pillow" aria-hidden />
            <span className="bedpick-side">{sideTitle(z)}</span>
            <span className="bedpick-sub">as you lie</span>
            {h?.name && <span className="bedpick-name"><i aria-hidden />{h.name}</span>}
            {h?.lines}
            {on && onChange && <span className="bedpick-tick" aria-hidden>✓</span>}
          </>;
          const cls = `bedpick-half${on ? " on" : ""}${h?.name ? " named" : ""}`;
          return onChange
            ? <button type="button" key={z} data-side={z} role="radio" aria-checked={on} aria-label={`${sideAria(z)}${h?.name ? `, ${h.name}` : ""}`}
                tabIndex={tabbable(z)} disabled={disabled} className={cls} style={style} onClick={() => pick(z)}>{body}</button>
            : <div key={z} className={cls} style={style} aria-label={`${sideAria(z)}${h?.name ? `, ${h.name}` : ""}`}>{body}</div>;
        })}
      </div>
    </div>
    {foot && <span className="bedpick-foot" aria-hidden>Seen from the foot of the bed</span>}
    {whole && onChange && <button type="button" data-side="both" role="radio" aria-checked={value === "both"} tabIndex={tabbable("both")} disabled={disabled}
      className={`bedpick-whole${value === "both" ? " on" : ""}`} onClick={() => pick("both")} onKeyDown={onKey}>{whole}</button>}
  </div>;
}

/**
 * "Check it: lie on your side for a moment." Watches the bed's own presence on each side (snapshot zones) and says
 * whether the bed sees you where you said you sleep. If it sees you on the other side instead, one tap fixes it:
 * swap sides when there are two of you, or move you to that side. Only a side that fills up after the check starts
 * counts, so a partner already asleep on the other side doesn't look like a mistake.
 */
export function SideCheck({ personId, onDone, doneLabel = "Done" }: { personId: string; onDone?: () => void; doneLabel?: string }) {
  const { snap } = useApp();
  const me = snap?.people.find(p => p.id === personId);
  const zones = snap?.zones;
  const pres = (z: Side): boolean | null => { const v = zones?.[z]; return v ? (v.bed_present ?? v.present) : null; };
  const start = useRef<Record<Side, boolean | null> | null>(null);
  const emptied = useRef<Record<Side, boolean>>({ left: false, right: false });
  const [verdict, setVerdict] = useState<"wait" | "ok" | "wrong" | "both">("wait");
  const [fixing, setFixing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  useEffect(() => { const t = window.setTimeout(() => setSlow(true), 90000); return () => clearTimeout(t); }, []);
  const l = pres("left"), r = pres("right");
  useEffect(() => {
    if (!me || !zones) return;
    if (!start.current) start.current = { left: l, right: r };
    if (l === false) emptied.current.left = true;
    if (r === false) emptied.current.right = true;
    const mine = me.zone as Side, other = otherSide(mine);
    const p = { left: l, right: r };
    const arrived = (z: Side) => p[z] === true && (start.current![z] !== true || emptied.current[z]);
    if (p[mine] && !p[other] && (arrived(mine) || start.current[other] !== true)) setVerdict("ok");
    else if (p[other] && !p[mine] && arrived(other)) setVerdict(v => v === "ok" ? v : "wrong");
    else if (p[mine] && p[other] && start.current.left && start.current.right && !emptied.current.left && !emptied.current.right) setVerdict(v => v === "wait" ? "both" : v);
  }, [l, r, me?.zone, !!zones]);
  if (!snap || !me) return null;
  const mine = me.zone as Side, other = otherSide(mine);
  const partner = snap.people.find(p => p.id !== me.id);
  const fix = async () => {
    setFixing(true); setErr(null);
    // after the change the person is where the bed sees them: whoever is lying there counts (set before the call, as
    // the live snapshot with the new sides can arrive before the answer does)
    const before = start.current;
    start.current = { left: false, right: false };
    setVerdict("wait");
    try {
      if (partner && partner.zone === other) await api.post("/api/household/swap-sides");
      else await api.put(`/api/people/${me.id}/profile`, { zone: other });
    } catch (e) { start.current = before; setVerdict("wrong"); setErr(`Couldn't change it: ${(e as Error).message}`); }
    setFixing(false);
  };
  const live: Partial<Record<Side, Half>> = {};
  for (const z of ["left", "right"] as Side[]) {
    const who = snap.people.find(p => p.zone === z);
    live[z] = { name: who?.name, color: who ? personaColor(who) : undefined,
      lines: <span className={`bedpick-live${pres(z) ? " in" : ""}`}>{pres(z) == null ? "Not sure" : pres(z) ? "Someone's here" : "Empty"}</span> };
  }
  return <div className="sidecheck" aria-live="polite">
    {!zones ? <p className="mute">This server is too old to check sides. Update it, then try again.</p> : <>
      <BedSidePicker value={null} halves={live} caption={null} label="What the bed sees on each side" />
      {verdict === "wait" && <p className="sidecheck-msg"><span className="spinner" aria-hidden /> Lie on your side ({sideTitle(mine).toLowerCase()}) for a moment. The bed takes a few seconds to notice.
        {slow && <><br /><small className="mute">Nothing yet. Some beds need a minute; keep still, or skip and check later in Settings.</small></>}</p>}
      {verdict === "ok" && <p className="sidecheck-msg good">We see you on the {sideTitle(mine).toLowerCase()} ✓</p>}
      {verdict === "both" && <p className="sidecheck-msg">Both sides look taken right now. Try it when the other side is empty for a moment.</p>}
      {verdict === "wrong" && <div className="sidecheck-wrong">
        <p className="sidecheck-msg warn">The bed sees you on the {sideTitle(other).toLowerCase()} (its "{other}" side). Swap?</p>
        <small className="mute">{partner && partner.zone === other ? `You'd be on the ${sideTitle(other).toLowerCase()} and ${partner.name} on the ${sideTitle(mine).toLowerCase()}. Plans, history, your watch and dial move with each of you.` : `You'd be on the ${sideTitle(other).toLowerCase()}.`}</small>
        <button type="button" className="btn pri big" disabled={fixing} onClick={fix}>{fixing ? "Changing…" : `Yes, I'm on the ${sideTitle(other).toLowerCase()}`}</button>
      </div>}
      {err && <p className="warn-text" role="alert">{err}</p>}
    </>}
    {onDone && <button type="button" className={verdict === "ok" ? "btn pri" : "link"} onClick={onDone}>{verdict === "ok" ? doneLabel : "Skip the check"}</button>}
  </div>;
}


/** Which way this bed names its sides (household; owner only). Only the left/right labels change. */
export function BedOrientation({ onSaid }: { onSaid?: (m: string) => void }) {
  const fromFoot = useBedFromFoot();
  return (
    <div className="row" style={{ justifyContent: "flex-start", gap: 8, flexWrap: "wrap", fontSize: 13, border: 0 }}>
      <span className="mute">Your bed calls its sides:</span>
      {([[false, "As you lie (most beds)"], [true, "From the foot of the bed"]] as const).map(([v, label]) =>
        <button key={String(v)} type="button" className="pill" aria-pressed={fromFoot === v} style={fromFoot === v ? { borderColor: "var(--acc)", color: "var(--acc)" } : undefined}
          onClick={async () => {
            if (fromFoot === v) return;
            try { await api.put("/api/bed-orientation", { from_foot: v }); setBedFromFoot(v); onSaid?.(v ? "Labels now follow a bed that names its sides from the foot" : "Labels now follow a bed that names its sides as you lie"); }
            catch (x) { onSaid?.(`Couldn't change it: ${(x as Error).message}`); }
          }}>{label}</button>)}
      <Tip label="What this changes" text="Only the left/right labels. Nobody's side, plans or history move. If the bed's 'left' is your right as you lie, pick 'From the foot of the bed'." />
    </div>
  );
}
