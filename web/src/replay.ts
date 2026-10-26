import { useEffect, useRef, useState } from "react";
import { API } from "./api";
import type { Live } from "./live";
import { type Anchor, playheadAt as advance, rebase, scoreboardAt, snapshotAt } from "./playback";
import { sampleFrames } from "./sample";
import type { Cue, Frame, Lang, MatchMeta, MatchState, Persona, RestartKind, TeamSummary } from "./types";

/** [t, ball x, ball y, players, ball height, restart]; replays packaged before a field was added omit it. */
export type PackedFrame = [number, number, number, ([number, number] | null)[], number?, ([RestartKind, "h" | "a"] | null)?];

interface ReplayPackage {
  matchId: string;
  meta: MatchMeta;
  playerIds: string[];
  frames: PackedFrame[];
  events: { event_id: string; clock_s: number; display_clock: string; type: string; team_id: string | null; outcome: string | null; period: number }[];
  cues: Cue[];
  /** The interpreter's state every 5 s of match clock; replays packaged before it was added omit it. */
  states?: Omit<MatchState, "id" | "meta">[];
}

export function unpackFrames(playerIds: string[], frames: PackedFrame[]): Frame[] {
  return frames.map(([t, bx, by, pl, bz, rs]) => ({
    t_s: t, ball: [bx, by, bz ?? 0],
    players: playerIds.flatMap((id, j) => (pl[j] ? [{ id, x: pl[j]![0], y: pl[j]![1] }] : [])),
    restart: rs ? { kind: rs[0], team: rs[1] } : null,
  }));
}

function team(meta: MatchMeta, side: "home" | "away", goals: number): TeamSummary {
  const t = meta[side];
  return { id: t.id, name: t.name, goals, shots: 0, shotsOnTarget: 0, xg: 0, passes: 0, passAccuracy: 0, finalThirdEntries: 0, ppda: null, pressures: 0, turnovers: 0 };
}

/** Plays a packaged replay with the same rendering contract as the live hook. */
export function useReplay(matchId: string | null, persona: Persona, lang: Lang, speed: number, finalState: MatchState | null, paused: boolean): Live {
  const pkg = useRef<ReplayPackage | null>(null);
  // Playback clock: wall time that stands still while paused. Pausing must not restart the replay,
  // so it lives here rather than in the playback effect's dependencies.
  const vclock = useRef<{ offset: number; pausedAt: number | null }>({ offset: 0, pausedAt: null });
  const vt = (now: number) => (vclock.current.pausedAt ?? now) - vclock.current.offset;
  const start = useRef<Anchor>({ wall: 0, t0: 0, speed });
  const shownAt = useRef<Map<string, number>>(new Map());
  const finalRef = useRef<MatchState | null>(finalState);
  finalRef.current = finalState;  // read inside the loop; changing it must not restart playback
  const view = useRef({ persona, lang });
  view.current = { persona, lang };  // same: switching viewer or language carries on from the current moment
  const frames = useRef<Frame[]>([]);
  const sampler = useRef<() => Frame | null>(() => null);
  const sample = useRef(() => sampler.current()).current;
  const [live, setLive] = useState<Live>({ connected: false, playhead: 0, liveEdge: 0, frame: null, state: null, cues: [], history: [], speed, sample });

  useEffect(() => {  // idempotent: StrictMode and Fast Refresh re-run effects
    const now = performance.now() / 1000;
    const c = vclock.current;
    if (paused && c.pausedAt === null) c.pausedAt = now;
    else if (!paused && c.pausedAt !== null) { c.offset += now - c.pausedAt; c.pausedAt = null; }
  }, [paused]);

  useEffect(() => {  // a new speed carries on from the current position instead of restarting
    const s = start.current;
    if (s.speed === speed) return;
    start.current = rebase(s, vt(performance.now() / 1000), speed);
  }, [speed]);

  useEffect(() => {
    if (!matchId) return;
    let stopped = false;
    pkg.current = null;
    shownAt.current = new Map();
    fetch(`${API}/api/replays/${matchId}`).then((r) => r.json()).then((p: ReplayPackage) => {
      if (stopped) return;
      pkg.current = p;
      frames.current = unpackFrames(p.playerIds, p.frames);
      start.current = { wall: vt(performance.now() / 1000), t0: p.frames[0]?.[0] ?? 0, speed: start.current.speed };
      setLive((l) => ({ ...l, connected: true }));
    }).catch((e) => console.warn("replay load failed", e));
    const playheadAt = (wall: number) => {
      const p = pkg.current;
      if (!p || !p.frames.length) return null;
      const now = vt(wall);
      const end = p.frames[p.frames.length - 1][0];
      const s = start.current;
      let playhead = advance(s, now);
      if (playhead > end + 20) { start.current = { ...s, wall: now, t0: p.frames[0][0] }; shownAt.current = new Map(); playhead = p.frames[0][0]; }
      return playhead;
    };
    sampler.current = () => {
      const ph = playheadAt(performance.now() / 1000);
      return ph === null ? null : sampleFrames(frames.current, ph);
    };
    let raf = 0;
    let last = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const nowMs = performance.now();
      if (nowMs - last < 66) return;
      last = nowMs;
      const p = pkg.current;
      if (!p || !p.frames.length) return;
      const playhead = playheadAt(nowMs / 1000)!;
      const now = vt(nowMs / 1000);  // cue lifetimes stand still while paused
      const frame = sampleFrames(frames.current, playhead)!;
      const { home: h, away: aw, period, clock } = scoreboardAt(p.events, playhead, p.meta.period_min ?? 45);
      // Stats follow the playhead; older packages without snapshots fall back to the full-time state.
      const fs = finalRef.current;
      const snap = snapshotAt(p.states, playhead);
      const base = p.states?.length ? (snap && { ...snap, id: p.matchId }) : fs && fs.id === p.matchId ? fs : null;
      const state: MatchState = base
        ? { ...base, home: { ...base.home, goals: h }, away: { ...base.away, goals: aw }, displayClock: clock, period, meta: p.meta }
        : { id: p.matchId, clockS: playhead, displayClock: clock, period, ended: false, home: team(p.meta, "home", h), away: team(p.meta, "away", aw),
            possessionHome: 0.5, momentum: 0, controlChaos: 0, topSpeeds: [], meta: p.meta };
      const active: Cue[] = [], history: Cue[] = [];
      const { persona, lang } = view.current;
      for (const c of p.cues) {
        if (c.display_at_s > playhead) continue;
        // Stamp every viewer/language variant as it airs, so after a switch the new view's copy of a
        // moment keeps its real air time: what is up stays up, and nothing already gone comes back.
        if (!shownAt.current.has(c.id)) shownAt.current.set(c.id, now);
        if (!(c.persona === persona || c.persona === "all") || !(c.lang === lang || c.lang === "all")) continue;
        history.push(c);
        if (now - (shownAt.current.get(c.id) ?? 0) < c.ttl_s) active.push(c);
      }
      history.sort((x, y) => y.display_at_s - x.display_at_s);
      active.sort((x, y) => x.priority - y.priority);
      setLive({ connected: true, playhead, liveEdge: playhead, frame, state, cues: active, history: history.slice(0, 40), speed: start.current.speed, sample });
    };
    raf = requestAnimationFrame(tick);
    return () => { stopped = true; cancelAnimationFrame(raf); sampler.current = () => null; };
  }, [matchId]);

  return live;
}
