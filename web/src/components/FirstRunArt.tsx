import { type CSSProperties, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";

/**
 * Decorative art for the first-run screens (sign-in, owner setup, the welcome): the animated brand mark, the
 * Ember / Tide background scene and the little card illustrations. CSS and inline SVG only (styles in firstrun.css);
 * everything here is aria-hidden. Motion is transform/opacity only, pauses while the page is hidden, and
 * prefers-reduced-motion swaps it for plain fades.
 */

export type ScenePersona = "ember" | "tide" | "neutral";
/** hero: the persona choice (everything moves) · brand: the first screen (slow drift) · quiet: later steps (still) */
export type SceneMode = "hero" | "brand" | "quiet" | "done";

/** Pause every first-run animation while the tab / app is in the background. */
export function usePauseWhenHidden() {
  useEffect(() => {
    const root = document.documentElement;
    const on = () => root.classList.toggle("et-hidden", document.visibilityState === "hidden");
    on();
    document.addEventListener("visibilitychange", on);
    return () => { document.removeEventListener("visibilitychange", on); root.classList.remove("et-hidden"); };
  }, []);
}

/** Two orbs, warm and cool, that drift together and breathe. */
export function BrandMark({ size = "md", className = "" }: { size?: "sm" | "md" | "lg"; className?: string }) {
  return <span className={`bm bm-${size} ${className}`} aria-hidden>
    <span className="bm-orb bm-e"><i /></span>
    <span className="bm-orb bm-t"><i /></span>
  </span>;
}

/** A seamless wave band: `n` periods of width `w`, crest height `a`, baseline `y`, filled down to `h`. */
function wave(y: number, a: number, w: number, n: number, h: number): string {
  let d = `M0 ${y} Q${w / 4} ${y - a} ${w / 2} ${y}`;
  for (let k = 1; k < n * 2; k++) d += ` T${(w / 2) * (k + 1)} ${y}`;
  return `${d} V${h} H0Z`;
}
const W1 = wave(60, 26, 800, 2, 400), W2 = wave(90, 20, 600, 2, 400), W3 = wave(120, 16, 500, 2, 400);

// a few embers only: each drifts up once every 7–11 s (positions fixed so it never looks random-busy)
const SPARKS: { x: number; d: number; t: number; s: number; dx: number }[] = [
  { x: 18, d: 0, t: 9, s: 5, dx: 14 }, { x: 31, d: 3.2, t: 10.5, s: 3, dx: -10 }, { x: 44, d: 1.4, t: 8, s: 4, dx: 8 },
  { x: 55, d: 5.1, t: 11, s: 3, dx: -14 }, { x: 63, d: 0.6, t: 9.5, s: 5, dx: 12 }, { x: 74, d: 2.6, t: 8.5, s: 3, dx: -6 },
  { x: 86, d: 4.3, t: 10, s: 4, dx: 10 },
];

/** The scene behind the whole first-time setup: a tide that rises and swells, or an ember glow that kindles. */
export function PersonaScene({ persona, mode }: { persona: ScenePersona; mode: SceneMode }) {
  return <div className="wl-scene" data-p={persona} data-m={mode} aria-hidden>
    <div className="sc-wash sc-wash-tide" />
    <div className="sc-wash sc-wash-ember" />
    <div className="sc-tide">
      {[W1, W2, W3].map((d, k) => <div key={k} className={`sc-layer sc-l${k + 1}`}>
        <svg className="sc-wave" viewBox={`0 0 ${[1600, 1200, 1000][k]} 400`} preserveAspectRatio="none"><path d={d} fill="currentColor" /></svg>
      </div>)}
    </div>
    <div className="sc-ember">
      <div className="sc-glowpos"><div className="sc-glow" /></div>
      <div className="sc-core" />
      {SPARKS.map((s, k) => <i key={k} className="sc-spark"
        style={{ "--x": `${s.x}%`, "--d": `${s.d}s`, "--t": `${s.t}s`, "--s": `${s.s}px`, "--dx": `${s.dx}px` } as CSSProperties} />)}
    </div>
  </div>;
}

/** Just the crest line of a wave (no fill), for the moonlit highlight on the front wave. */
function crest(y: number, a: number, w: number, n: number): string {
  let d = `M0 ${y} Q${w / 4} ${y - a} ${w / 2} ${y}`;
  for (let k = 1; k < n * 2; k++) d += ` T${(w / 2) * (k + 1)} ${y}`;
  return d;
}
// Tide card: three swells, each a whole number of periods wider than the card so the drift loops seamlessly
const GLINTS: [number, number, number][] = [[117, 57, 10], [120, 61.5, 5], [115, 66, 13], [119.5, 71, 7], [116, 77, 11], [121, 83, 6]];
const T1 = wave(56, 6, 80, 4, 96), T2 = wave(66, 6, 64, 5, 96), T3 = wave(78, 5, 100, 4, 96), T3C = crest(78, 5, 100, 4);
// Ember card: a heap of charcoal over a glowing bed. Each chunk is dark on top and lit from below, with a hot rim
// along its lower edge; three rows, back to front (points in the 160x96 box).
const CHUNKS: string[][] = [
  ["28,80 31,71 38,66 47,68 51,75 49,81", "50,78 53,68 61,63 70,64 75,70 74,79", "74,78 77,67 86,62 96,64 100,71 98,79", "98,79 102,70 110,66 119,69 122,77 120,81", "119,81 123,73 131,71 138,75 139,82"],
  ["14,90 17,80 25,75 34,77 39,84 37,91", "37,89 40,78 49,73 59,74 64,81 62,90", "61,89 65,77 75,71 86,73 91,80 89,90", "88,90 92,79 101,74 111,76 115,83 113,91", "112,91 116,81 125,77 135,79 140,86 138,92"],
  ["4,97 7,90 15,85 24,87 28,97", "24,97 28,88 38,83 49,85 53,97", "50,97 54,87 64,82 75,84 80,97", "78,97 82,86 93,82 103,84 107,97", "104,97 108,88 118,84 128,86 132,97", "129,97 133,89 142,86 151,90 154,97"],
];
// glowing cracks (they pulse a little out of step)
const CRACKS = ["19,87 23,84 26,85", "45,83 49,80 53,81", "70,79 74,77 77,79 80,76", "97,84 101,81 104,82", "121,87 125,84 128,85", "60,93 64,90 67,91", "89,93 93,91 96,92", "56,73 60,71"];
// tiny bright bits showing between chunks
const BITS: [number, number, number][] = [[62, 82, 1.2], [88, 81, 1.1], [37, 84, 1], [113, 85, 1]];

/** The card illustrations for the persona choice: a moonlit tide, a bed of glowing coals, and the two meeting. */
export function PersonaArt({ kind }: { kind: ScenePersona }) {
  if (kind === "tide") return <span className="pa pa-tide" aria-hidden>
    <svg viewBox="0 0 160 96" preserveAspectRatio="xMidYMax slice">
      <defs>
        <radialGradient id="pa-t-halo" cx="122" cy="22" r="26" gradientUnits="userSpaceOnUse">
          <stop offset="0" className="pt-h0" /><stop offset="1" className="pt-h1" />
        </radialGradient>
        <radialGradient id="pa-t-glow" cx=".5" cy=".5" r=".5">
          <stop offset="0" className="pt-g0" /><stop offset="1" className="pt-g1" />
        </radialGradient>
        <mask id="pa-t-cres"><rect x="100" y="0" width="44" height="44" fill="#fff" /><circle cx="125.8" cy="19.6" r="6.3" fill="#000" /></mask>
        <linearGradient id="pa-t-front" x1="0" y1="72" x2="0" y2="96" gradientUnits="userSpaceOnUse">
          <stop offset="0" className="pt-f0" /><stop offset="1" className="pt-f1" />
        </linearGradient>
      </defs>
      <g className="pa-stars"><circle cx="24" cy="15" r=".9" /><circle cx="46" cy="29" r=".7" /><circle cx="82" cy="12" r=".8" /><circle cx="96" cy="33" r=".6" /></g>
      <ellipse className="pt-glow" cx="118" cy="74" rx="70" ry="34" fill="url(#pa-t-glow)" />
      <circle className="pt-halo" cx="122" cy="22" r="26" fill="url(#pa-t-halo)" />
      <circle className="pt-moon" cx="122" cy="22" r="7.5" mask="url(#pa-t-cres)" />
      <path className="pa-w pa-w1" d={T1} />
      <path className="pa-w pa-w2" d={T2} />
      <g className="pa-w pa-w3"><path d={T3} fill="url(#pa-t-front)" /><path className="pt-crest" d={T3C} /></g>
      {/* the moon's path on the water: short glints under it that shimmer */}
      <g className="pt-glints">{GLINTS.map(([x, y, w], k) => <rect key={k} x={x} y={y} width={w} height="1.1" rx=".55" />)}</g>
    </svg>
    <span className="pa-ring" />
  </span>;
  if (kind === "ember") return <span className="pa pa-ember" aria-hidden>
    <svg viewBox="0 0 160 96" preserveAspectRatio="xMidYMax slice">
      <defs>
        <radialGradient id="pa-e-glow" cx="80" cy="98" r="92" gradientUnits="userSpaceOnUse">
          <stop offset="0" className="pe-g0" /><stop offset=".42" className="pe-g1" /><stop offset="1" className="pe-g2" />
        </radialGradient>
        <radialGradient id="pa-e-bed" cx=".5" cy=".55" r=".5">
          <stop offset="0" stopColor="#fff1c2" /><stop offset=".3" stopColor="#ffb257" /><stop offset=".62" stopColor="#e5622a" stopOpacity=".85" /><stop offset="1" stopColor="#b8441c" stopOpacity="0" />
        </radialGradient>
        {[0, 1, 2].map(r => <linearGradient key={r} id={`pa-e-chunk${r}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className="pe-k0" /><stop offset=".5" className="pe-k1" /><stop offset="1" className={`pe-k2 pe-k2-${r}`} />
        </linearGradient>)}
        <linearGradient id="pa-e-rim" x1="0" y1="0" x2="0" y2="1">
          <stop offset=".35" stopColor="#ffb35e" stopOpacity="0" /><stop offset="1" stopColor="#ffc576" />
        </linearGradient>
      </defs>
      <g className="pa-stars"><circle cx="20" cy="14" r=".8" /><circle cx="41" cy="26" r=".6" /><circle cx="128" cy="12" r=".9" /><circle cx="143" cy="30" r=".6" /></g>
      <rect className="pe-halo" x="-20" y="0" width="200" height="96" fill="url(#pa-e-glow)" />
      <ellipse className="pe-bed" cx="80" cy="86" rx="82" ry="26" fill="url(#pa-e-bed)" />
      {CHUNKS.map((row, r) => row.map((pts, k) => <g key={`${r}-${k}`} className={`pe-row${r}`}>
        <polygon className="pe-chunk" points={pts} fill={`url(#pa-e-chunk${r})`} />
        <polygon className="pe-rim" points={pts} />
      </g>))}
      {BITS.map(([x, y, r], k) => <circle key={`s${k}`} className={`pe-bit pe-s${k % 3}`} cx={x} cy={y} r={r} />)}
      {CRACKS.map((pts, k) => <polyline key={`c${k}`} className={`pe-crack pe-c${k % 3}`} points={pts} />)}
      <g className="pe-sparks"><circle className="pe-sp pe-sp1" cx="66" cy="74" r="1.3" /><circle className="pe-sp pe-sp2" cx="86" cy="72" r="1" /><circle className="pe-sp pe-sp3" cx="100" cy="76" r="1.2" /><circle className="pe-sp pe-sp4" cx="54" cy="77" r=".9" /></g>
    </svg>
    <span className="pa-ring" />
  </span>;
  // in between: warm and cool meeting along a gentle wave, a coal glow on one side and a ripple on the other
  return <span className="pa-mid" aria-hidden>
    <svg viewBox="0 0 44 44">
      <defs>
        <clipPath id="pa-m-clip"><circle cx="22" cy="22" r="20" /></clipPath>
        <radialGradient id="pa-m-warm" cx="8" cy="40" r="34" gradientUnits="userSpaceOnUse">
          <stop offset="0" className="pm-w0" /><stop offset=".55" className="pm-w1" /><stop offset="1" className="pm-w2" />
        </radialGradient>
        <linearGradient id="pa-m-cool" x1="0" y1="0" x2="0" y2="44" gradientUnits="userSpaceOnUse">
          <stop offset="0" className="pm-c0" /><stop offset="1" className="pm-c1" />
        </linearGradient>
      </defs>
      <g clipPath="url(#pa-m-clip)">
        <g className="pm-sway">
          <rect x="-6" y="-6" width="56" height="56" fill="url(#pa-m-cool)" />
          <path d="M-6 -6 H25 C31 6 15 16 22 26 C27 33 18 40 21 50 H-6 Z" fill="url(#pa-m-warm)" />
        </g>
        <path className="pm-ripple" d="M24 31 q3 -2.4 6 0 t6 0 t6 0" />
        <path className="pm-ripple pm-r2" d="M26 36.5 q3 -2.2 6 0 t6 0" />
        <circle className="pm-coal" cx="12" cy="31" r="3.2" />
      </g>
      <circle className="pm-ring" cx="22" cy="22" r="20.5" />
    </svg>
  </span>;
}

/**
 * The hand-off from setup to Tonight, in the colours they picked: Tide's cool water sweeps up and passes over,
 * Ember's warm glow blooms with a few rising sparks, In between has warm and cool meeting from either side. `phase`
 * "in" covers the screen, "out" reveals what's underneath; the parent drives the timing and removes it after.
 */
export function Finale({ persona, phase, name }: { persona: ScenePersona; phase: "in" | "out"; name?: string }) {
  return <div className={`fin fin-${persona}`} data-phase={phase} aria-hidden>
    {persona === "tide" && <div className="fin-sea">
      {[0, 1, 2].map(k => <div key={k} className={`fin-sheet fin-s${k}`}>
        <svg className="fin-edge" viewBox="0 0 800 60" preserveAspectRatio="none"><path d={wave(30, 18 - k * 4, 400 - k * 70, 4, 60)} /><path className="fin-crest" d={crest(30, 18 - k * 4, 400 - k * 70, 4)} /></svg>
        <div className="fin-water" />
        <svg className="fin-edge fin-edge-b" viewBox="0 0 800 60" preserveAspectRatio="none"><path d={wave(30, 16 - k * 4, 360 - k * 60, 4, 60)} /></svg>
      </div>)}
    </div>}
    {persona === "ember" && <div className="fin-fire">
      <div className="fin-bloom" />
      <div className="fin-bloom fin-b2" />
      {SPARKS.concat(SPARKS.slice(0, 5).map(s => ({ ...s, x: (s.x + 37) % 96 + 2, dx: -s.dx }))).map((s, k) => <i key={k} className="fin-spark"
        style={{ "--x": `${s.x}%`, "--d": `${((k * 0.173) % 0.62).toFixed(2)}s`, "--s": `${s.s}px`, "--dx": `${s.dx * 2}px` } as CSSProperties} />)}
    </div>}
    {persona === "neutral" && <div className="fin-meet">
      <div className="fin-half fin-warm" />
      <div className="fin-half fin-cool" />
    </div>}
    <p className="fin-word">{name ? `Sleep well, ${name}` : "Sleep well"}</p>
  </div>;
}

const COVER_MS = 950, REVEAL_MS = 1250;

/**
 * Plays the hand-off over the whole phone view. Resolves once the screen is covered (the caller then swaps in
 * Tonight underneath), and removes itself after the reveal. It lives outside the React tree on purpose: the welcome
 * unmounts halfway through. Never blocks: no host, a hidden page or any error resolves at once and removes it.
 */
export function playFinale(persona: ScenePersona, name?: string): Promise<void> {
  return new Promise(resolve => {
    const host = document.querySelector(".app");
    if (!host || document.visibilityState === "hidden") { resolve(); return; }
    const el = document.createElement("div");
    el.className = "fin-host";
    host.appendChild(el);
    let root: Root | null = null;
    let gone = false;
    const timers: number[] = [];
    const end = () => {
      if (gone) return;
      gone = true;
      timers.forEach(t => clearTimeout(t));
      document.removeEventListener("visibilitychange", vis);
      try { root?.unmount(); } catch { /* already gone */ }
      el.remove();
      resolve();
    };
    const vis = () => { if (document.visibilityState === "hidden") end(); };   // backgrounded: just show Tonight
    document.addEventListener("visibilitychange", vis);
    try {
      root = createRoot(el);
      root.render(<Finale persona={persona} phase="in" name={name} />);
    } catch { end(); return; }
    timers.push(window.setTimeout(() => {
      resolve();
      try { root?.render(<Finale persona={persona} phase="out" name={name} />); } catch { end(); }
    }, COVER_MS));
    timers.push(window.setTimeout(end, COVER_MS + REVEAL_MS));
  });
}

/**
 * Keeps a focused text field (and its form's button) above the on-screen keyboard. A phone browser shrinks only the
 * visual viewport when the keyboard opens, so the field can end up underneath it: this sets --kb on <html> to the
 * keyboard's height (for bottom padding) and scrolls the field into view. Harmless where the page itself resizes
 * (the Android app), since --kb is then 0.
 */
export function useKeyboardRoom() {
  useEffect(() => {
    const vv = window.visualViewport;
    const root = document.documentElement;
    const set = () => {
      const kb = vv ? Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)) : 0;
      root.style.setProperty("--kb", `${kb > 80 ? kb : 0}px`);   // small differences are browser chrome, not a keyboard
    };
    let t = 0;
    const onFocus = (e: FocusEvent) => {
      const el = e.target as HTMLElement | null;
      if (!el || !el.matches("input:not([type=checkbox]):not([type=radio]), textarea, select")) return;
      window.clearTimeout(t);
      // after the keyboard has had time to open
      t = window.setTimeout(() => { set(); el.scrollIntoView({ block: "center", behavior: "smooth" }); }, 320);
    };
    set();
    vv?.addEventListener("resize", set);
    document.addEventListener("focusin", onFocus);
    return () => { vv?.removeEventListener("resize", set); document.removeEventListener("focusin", onFocus); window.clearTimeout(t); root.style.removeProperty("--kb"); };
  }, []);
}

/** "You're set": a ring that draws itself round a check, once. */
export function FinishMark() {
  return <span className="wl-finish" aria-hidden>
    <span className="fin-glow" />
    <svg viewBox="0 0 120 120">
      <circle className="fin-track" cx="60" cy="60" r="46" />
      <circle className="fin-ring" cx="60" cy="60" r="46" pathLength={1} />
      <path className="fin-check" d="M41 61 L54 74 L80 47" pathLength={1} />
    </svg>
  </span>;
}
