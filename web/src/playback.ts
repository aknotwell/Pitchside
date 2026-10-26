// Pure replay maths, kept out of the React hook so it can be unit tested.

export interface ReplayEvent { clock_s: number; type: string; team_id: string | null; outcome: string | null; period: number }

/** Playhead anchor: at playback time `wall` the replay was at match time `t0`, moving at `speed`. */
export interface Anchor { wall: number; t0: number; speed: number }

export const playheadAt = (a: Anchor, now: number) => a.t0 + (now - a.wall) * a.speed;

/** A new speed carries on from the current position instead of restarting. */
export const rebase = (a: Anchor, now: number, speed: number): Anchor => ({ wall: now, t0: playheadAt(a, now), speed });

/** Broadcast clock at match time `t`: the simulator's rule (`display_clock` in sim/engine.py), so the
 *  replay clock runs every second instead of jumping from one event's clock to the next. */
export function displayClock(t: number, period: number, periodStart: number, periodMin: number): string {
  const regulation = periodMin * 60;
  const base = period === 1 ? 0 : regulation;
  const elapsed = Math.max(0, t - periodStart);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (elapsed <= regulation) { const total = base + elapsed; return `${pad(Math.floor(total / 60))}:${pad(Math.floor(total % 60))}`; }
  const extra = elapsed - regulation;
  return `${Math.floor((base + regulation) / 60)}+${Math.floor(extra / 60) + 1}:${pad(Math.floor(extra % 60))}`;
}

/** Score, period and clock at `playhead`, from the events (sorted by clock_s) up to it. */
export function scoreboardAt(events: ReplayEvent[], playhead: number, periodMin: number) {
  let home = 0, away = 0, period = 1, periodStart = 0, periodEnd: number | null = null;
  for (const e of events) {
    if (e.clock_s > playhead) break;
    if (e.type === "shot" && e.outcome === "goal") { if (e.team_id === "h") home++; else away++; }
    // Period comes from the whistles only: an event resolving on the half-time whistle can carry the next period
    if (e.type === "period_start") { period = e.period; periodStart = e.clock_s; periodEnd = null; }
    if (e.type === "period_end") periodEnd = e.clock_s;
  }
  // The clock stops at the whistle and holds until the next period starts
  const clock = displayClock(Math.min(playhead, periodEnd ?? playhead), period, periodStart, periodMin);
  return { home, away, period, clock };
}

/** The latest of `snapshots` (sorted by clockS) at or before `playhead`, or null before the first. */
export function snapshotAt<T extends { clockS: number }>(snapshots: T[] | undefined, playhead: number): T | null {
  if (!snapshots?.length || snapshots[0].clockS > playhead) return null;
  let lo = 0, hi = snapshots.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (snapshots[mid].clockS <= playhead) lo = mid; else hi = mid - 1;
  }
  return snapshots[lo];
}
