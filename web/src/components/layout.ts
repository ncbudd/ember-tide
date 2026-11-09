import { useEffect, useState } from "react";
import { isApp } from "../api";

/**
 * Which shape of screen the phone app is on, for the tablet and foldable layouts.
 *
 * - "tab": a tablet or an unfolded foldable (at least 700 px wide and touch, the Android app, or the installed
 *   home-screen app). Desktop browsers keep the phone frame (they open Studio anyway).
 * - "land" / "port": orientation.
 * - fold "h": a hinge running top to bottom (book posture, two side-by-side halves); "v": a hinge running left to
 *   right (tabletop posture, top and bottom halves). `segA` is the size of the first half and `hinge` the gap,
 *   in CSS pixels, so content can be kept off the fold.
 *
 * `?layout=tablet` or `?layout=phone` overrides the detection and is remembered on this device (handy for a
 * kitchen kiosk or for testing); `?layout=auto` clears it.
 */
export interface Layout { tab: boolean; land: boolean; fold: "h" | "v" | null; segA: number; hinge: number }

const KEY = "et-layout";

function override(): "tablet" | "phone" | null {
  try {
    const q = new URLSearchParams(location.search).get("layout");
    if (q === "auto") localStorage.removeItem(KEY);
    else if (q === "tablet" || q === "phone") localStorage.setItem(KEY, q);
    const v = localStorage.getItem(KEY);
    return v === "tablet" || v === "phone" ? v : null;
  } catch { return null; }
}

/** The display's segments: the Viewport Segments API when there is one, else the CSS env() values. */
function segments(): DOMRect[] {
  const vp = (window as unknown as { viewport?: { segments?: DOMRect[] | null } }).viewport;
  if (vp?.segments && vp.segments.length > 1) return vp.segments;
  const two = (q: string) => window.matchMedia?.(q).matches;
  const h = two("(horizontal-viewport-segments: 2)"), v = two("(vertical-viewport-segments: 2)");
  if (!h && !v) return [];
  const probe = document.createElement("div");
  probe.style.cssText = "position:fixed;visibility:hidden;pointer-events:none;"
    + "left:env(viewport-segment-left 0 0,0px);top:env(viewport-segment-top 0 0,0px);"
    + "width:env(viewport-segment-width 0 0,0px);height:env(viewport-segment-height 0 0,0px)";
  const probe2 = document.createElement("div");
  probe2.style.cssText = "position:fixed;visibility:hidden;pointer-events:none;"
    + `left:env(viewport-segment-left ${h ? "1 0" : "0 1"},0px);top:env(viewport-segment-top ${h ? "1 0" : "0 1"},0px);`
    + `width:env(viewport-segment-width ${h ? "1 0" : "0 1"},0px);height:env(viewport-segment-height ${h ? "1 0" : "0 1"},0px)`;
  document.body.append(probe, probe2);
  const a = probe.getBoundingClientRect(), b = probe2.getBoundingClientRect();
  probe.remove(); probe2.remove();
  return a.width && b.width ? [a, b] : [];
}

interface NativeFold { fold: boolean; x: number; y: number; width: number; height: number; orientation: "vertical" | "horizontal"; state: string; separating: boolean }

/** The hinge as the Android app reports it, only when it actually splits the screen (book or tabletop, or a real gap). */
function nativeFold(): NativeFold | null {
  const f = (window as unknown as { __etFold?: NativeFold }).__etFold;
  return f && f.fold && (f.separating || f.state === "half-opened") ? f : null;
}

function measure(): Layout {
  const w = window.innerWidth, hgt = window.innerHeight;
  const coarse = window.matchMedia?.("(pointer: coarse)").matches ?? false;
  const installed = window.matchMedia?.("(display-mode: standalone)").matches ?? false;
  const o = override();
  const segs = segments();
  let fold: Layout["fold"] = null, segA = 0, hinge = 0;
  // testing aid: ?fold=h:420:30 pretends there's a hinge (direction : first half in px : hinge width in px)
  const sim = (() => { try { const q = new URLSearchParams(location.search).get("fold"); if (q !== null) sessionStorage.setItem("et-fold", q); return sessionStorage.getItem("et-fold"); } catch { return null; } })();
  const m = sim?.match(/^([hv]):(\d+):(\d+)$/);
  if (m) { fold = m[1] as "h" | "v"; segA = +m[2]; hinge = +m[3]; }
  else if (segs.length !== 2 && nativeFold()) {
    // older Android WebViews have no Viewport Segments: the app reports the hinge itself (MainActivity → et-fold)
    const f = nativeFold()!;
    if (f.orientation === "vertical") { fold = "h"; segA = f.x; hinge = f.width; }
    else { fold = "v"; segA = f.y; hinge = f.height; }
  }
  else
  if (segs.length === 2) {
    const [a, b] = segs;
    if (Math.abs(a.top - b.top) < 2) { fold = "h"; segA = a.width; hinge = Math.max(0, b.left - a.right); }
    else { fold = "v"; segA = a.height; hinge = Math.max(0, b.top - a.bottom); }
  }
  const tab = o ? o === "tablet" && w >= 600 : (w >= 700 && (coarse || isApp() || installed)) || fold !== null;
  return { tab, land: w > hgt, fold, segA, hinge };
}

export function useLayout(): Layout {
  const [l, setL] = useState<Layout>(() => measure());
  useEffect(() => {
    let raf = 0;
    const on = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => setL(measure())); };
    window.addEventListener("resize", on);
    window.addEventListener("orientationchange", on);
    const vp = (window as unknown as { viewport?: EventTarget }).viewport;
    vp?.addEventListener?.("segmentschange", on);
    window.addEventListener("et-fold", on);
    return () => { window.removeEventListener("resize", on); window.removeEventListener("orientationchange", on); vp?.removeEventListener?.("segmentschange", on); window.removeEventListener("et-fold", on); cancelAnimationFrame(raf); };
  }, []);
  return l;
}

/** Class names and CSS variables for the app shell. */
export function layoutProps(l: Layout): { className: string; style: Record<string, string> } {
  const cls = [l.tab ? "tab" : "", l.tab ? (l.land ? "land" : "port") : "", l.fold ? `fold-${l.fold}` : ""].filter(Boolean).join(" ");
  return { className: cls, style: l.fold ? { "--seg-a": `${Math.round(l.segA)}px`, "--hinge": `${Math.round(l.hinge)}px` } : {} };
}
