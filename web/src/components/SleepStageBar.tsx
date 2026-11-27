import type { CSSProperties } from "react";

export type SleepStage = "deep" | "light" | "rem" | "awake";
export type StageTotals = Partial<Record<SleepStage, number>>;

export const STAGE_ORDER: SleepStage[] = ["deep", "light", "rem", "awake"];
export const STAGE_COLORS: Record<string, string> = {
  awake: "#e3b37a", rem: "#a08be2", light: "#7fb6cc", deep: "#3f6fb5",
};

export function stageTotal(totals?: StageTotals | null): number {
  return STAGE_ORDER.reduce((sum, stage) => {
    const value = totals?.[stage];
    return sum + (typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0);
  }, 0);
}

export function stageLabel(totals: StageTotals, from?: string | null): string {
  const names: Record<SleepStage, string> = { deep: "Deep", light: "Light", rem: "REM", awake: "Awake" };
  const prefix = from ? `${from} stage totals` : "Stage totals";
  return `${prefix}: ${STAGE_ORDER.filter(stage => (totals[stage] ?? 0) > 0)
    .map(stage => `${names[stage]} ${Math.round(totals[stage] ?? 0)} min`).join(", ")}`;
}

export default function SleepStageBar({
  totals, from, vertical = false, scale, className = "",
}: {
  totals?: StageTotals | null;
  from?: string | null;
  vertical?: boolean;
  scale?: number;
  className?: string;
}) {
  const total = stageTotal(totals);
  if (!totals || total <= 0) return null;
  const style: CSSProperties = vertical && scale && scale > 0
    ? { height: `${Math.min(100, total / scale * 100)}%` }
    : {};
  const label = stageLabel(totals, from);
  return (
    <span className={`stage-stack ${vertical ? "vertical" : "horizontal"} ${className}`.trim()}
      style={style} role="img" aria-label={label} title={label}>
      {STAGE_ORDER.filter(stage => (totals[stage] ?? 0) > 0).map(stage => (
        <span key={stage} style={{ backgroundColor: STAGE_COLORS[stage], flexGrow: totals[stage] }} />
      ))}
    </span>
  );
}
