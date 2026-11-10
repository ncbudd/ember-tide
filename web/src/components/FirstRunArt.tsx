import { type CSSProperties, useEffect } from "react";

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

const A1 = wave(52, 7, 80, 4, 90), A2 = wave(62, 6, 64, 5, 90), A3 = wave(72, 5, 100, 4, 90);

/** The card illustrations for the persona choice. */
export function PersonaArt({ kind }: { kind: ScenePersona }) {
  if (kind === "tide") return <span className="pa pa-tide" aria-hidden>
    <span className="pa-moon" />
    <svg viewBox="0 0 160 90" preserveAspectRatio="none">
      <path className="pa-w pa-w1" d={A1} fill="currentColor" />
      <path className="pa-w pa-w2" d={A2} fill="currentColor" />
      <path className="pa-w pa-w3" d={A3} fill="currentColor" />
    </svg>
    <span className="pa-ring" />
  </span>;
  if (kind === "ember") return <span className="pa pa-ember" aria-hidden>
    <span className="pa-halo" />
    <span className="pa-coal" />
    <i className="pa-spark" /><i className="pa-spark" /><i className="pa-spark" />
    <span className="pa-ring" />
  </span>;
  return <span className="pa-mid" aria-hidden />;
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
