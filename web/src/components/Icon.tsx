/** Small line icons drawn as SVG, so they look the same on every phone. (Symbol characters like ⏻ or ⏸ are
 *  missing from some Android fonts, or turn into colour emoji.) They take the current text colour. */
export type IconName = "plusminus" | "snow" | "sun" | "power" | "moon" | "pause";

export default function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden focusable="false" style={{ display: "block" }}>
      {name === "plusminus" && <g {...p}><path d="M12 4v9M7.5 8.5h9" /><path d="M7.5 18.5h9" /></g>}
      {name === "snow" && <g {...p}><path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9" /><path d="M9.5 4.5 12 7l2.5-2.5M9.5 19.5 12 17l2.5 2.5" /></g>}
      {name === "sun" && <g {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" /></g>}
      {name === "power" && <g {...p}><path d="M12 3v8" /><path d="M7.2 6.3a7.5 7.5 0 1 0 9.6 0" /></g>}
      {name === "moon" && <g {...p}><path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10Z" /></g>}
      {name === "pause" && <g {...p}><path d="M9 6v12M15 6v12" /></g>}
    </svg>
  );
}
