// Pure replay maths, kept out of the React hook so it can be unit tested.

export interface ReplayEvent { clock_s: number; type: string; team_id: string | null; outcome: string | null; period: number }

/** Playhead anchor: at playback time `wall` the replay was at match time `t0`, moving at `speed`. */
export interface Anchor { wall: number; t0: number; speed: number }

export const playheadAt = (a: Anchor, now: number) => a.t0 + (now - a.wall) * a.speed;

/** A new speed carries on from the current position instead of restarting. */
export const rebase = (a: Anchor, now: number, speed: number): Anchor => ({ wall: now, t0: playheadAt(a, now), speed });

/** Match seconds per wall second a viewer can pick, for live and replay alike. */
export const SPEEDS = [1, 2, 3, 4, 10, 20];

/** One viewer's transport. While `followLive`, the playhead rides the delayed live edge and the
 *  anchor is ignored; while `paused`, it holds at `anchor.t0`. */
export interface Transport { anchor: Anchor; paused: boolean; followLive: boolean }

/** Match time the player can show. Live: first recorded frame to the delayed live edge, which advances
 *  at `rate` match s per wall s (0 once it stops, e.g. at full time). Replay: the whole match. */
export interface Timeline { start: number; end: number; live: boolean; rate: number }

/** How far past the end a replay runs on before starting over, so the final whistle can be seen. */
const REPLAY_TAIL_S = 20;

const clamp = (t: number, tl: Timeline) => Math.min(tl.end, Math.max(tl.start, t));
const following = (tr: Transport, tl: Timeline) => tl.live && tr.followLive;
const unclamped = (tr: Transport, now: number) => (tr.paused ? tr.anchor.t0 : playheadAt(tr.anchor, now));

export const nearestSpeed = (rate: number) =>
  rate > 0 ? SPEEDS.reduce((a, b) => (Math.abs(Math.log(b / rate)) < Math.abs(Math.log(a / rate)) ? b : a)) : 1;

/** Speed the viewer is watching at: the simulator's own pace while riding the live edge. */
export const speedOf = (tr: Transport, tl: Timeline) => (following(tr, tl) ? nearestSpeed(tl.rate) : tr.anchor.speed);

export const positionAt = (tr: Transport, now: number, tl: Timeline) => (following(tr, tl) ? tl.end : clamp(unclamped(tr, now), tl));

const hold = (tr: Transport, now: number, tl: Timeline, t: number, speed = speedOf(tr, tl)): Transport =>
  ({ anchor: { wall: now, t0: clamp(t, tl), speed }, paused: tr.paused, followLive: false });

export const goLive = (tr: Transport, now: number, tl: Timeline): Transport =>
  ({ anchor: { wall: now, t0: tl.end, speed: tr.anchor.speed }, paused: false, followLive: true });

export const pause = (tr: Transport, now: number, tl: Timeline): Transport =>
  ({ ...hold(tr, now, tl, positionAt(tr, now, tl)), paused: true });

export const play = (tr: Transport, now: number, tl: Timeline): Transport =>
  ({ ...hold(tr, now, tl, positionAt(tr, now, tl)), paused: false });

export const togglePause = (tr: Transport, now: number, tl: Timeline) => (tr.paused ? play : pause)(tr, now, tl);

/** Dragging a live match's bar to the right end (within a wall second of it) goes back to live. */
export const seek = (tr: Transport, now: number, tl: Timeline, t: number): Transport =>
  (tl.live && t >= tl.end - Math.max(1, tl.rate) ? goLive(tr, now, tl) : hold(tr, now, tl, t));

export const setSpeed = (tr: Transport, now: number, tl: Timeline, speed: number): Transport =>
  hold(tr, now, tl, positionAt(tr, now, tl), speed);

/** Advances the transport's own rules: playing at least as fast as the live edge until reaching it
 *  rejoins live, and a replay that has run past its end starts over. Returns `tr` when nothing changes. */
export function settle(tr: Transport, now: number, tl: Timeline): Transport {
  if (tr.paused || tr.followLive) return tr;
  const t = unclamped(tr, now);
  if (tl.live) return t >= tl.end && tr.anchor.speed >= 0.9 * tl.rate ? goLive(tr, now, tl) : tr;
  return t > tl.end + REPLAY_TAIL_S ? { ...tr, anchor: { ...tr.anchor, wall: now, t0: tl.start } } : tr;
}

/** Cues the playhead crossed playing forward from `prev` to `cur`. A jump (`prev` null) or a step
 *  backwards crosses nothing, so seeking doesn't pop up every cue it skipped over. */
export const crossed = <C extends { display_at_s: number }>(cues: Iterable<C>, prev: number | null, cur: number): C[] =>
  prev === null || cur < prev ? [] : [...cues].filter((c) => c.display_at_s > prev && c.display_at_s <= cur);

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
