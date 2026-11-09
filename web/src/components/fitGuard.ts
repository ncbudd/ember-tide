/**
 * A brake for "measure, then resize" code (Tonight's auto-fit, the Today card, charts that fill their box).
 * Two such adjusters on one screen can push each other back and forth forever; React then stops with "maximum
 * update depth exceeded" and the screen goes blank. Each adjuster asks `allow()` before changing anything: after
 * 15 changes within a second it says no until the window size changes, so the layout settles instead of crashing.
 */
export interface FitGuard { times: number[]; lockedFor: string }

export const fitGuard = (): FitGuard => ({ times: [], lockedFor: "" });

export function allow(g: FitGuard): boolean {
  const size = `${window.innerWidth}x${window.innerHeight}`;
  if (g.lockedFor) {
    if (g.lockedFor === size) return false;
    g.lockedFor = "";
    g.times = [];
  }
  const now = performance.now();
  g.times = g.times.filter(t => now - t < 1000);
  g.times.push(now);
  if (g.times.length > 15) {
    g.lockedFor = size;
    return false;
  }
  return true;
}
