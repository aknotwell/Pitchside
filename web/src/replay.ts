import { useEffect, useRef } from "react";
import { API } from "./api";
import { scoreboardAt, snapshotAt, type Timeline } from "./playback";
import { type Feed, type Live, usePlayer } from "./player";
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

/** Plays a packaged replay with the same transport and rendering contract as the live hook. */
export function useReplay(matchId: string | null, persona: Persona, lang: Lang, finalState: MatchState | null): Live {
  const feed = useRef<Feed | null>(null);
  const finalRef = useRef<MatchState | null>(finalState);
  finalRef.current = finalState;  // read inside the loop; changing it must not restart playback
  const view = useRef({ persona, lang });
  view.current = { persona, lang };  // same: switching viewer or language carries on from the current moment

  useEffect(() => {
    if (!matchId) return;
    let stopped = false;
    feed.current = null;
    fetch(`${API}/api/replays/${matchId}`).then((r) => r.json()).then((p: ReplayPackage) => {
      if (stopped || !p.frames.length) return;
      const frames = unpackFrames(p.playerIds, p.frames);
      const timeline: Timeline = { start: frames[0].t_s, end: frames[frames.length - 1].t_s, live: false, rate: 0 };
      feed.current = {
        connected: true, frames, cues: p.cues, late: [],
        timeline: () => timeline,
        // Every viewer/language variant airs together, so after a switch the new view's copy of a
        // moment keeps its real air time: what is up stays up, and nothing already gone comes back.
        shows: (c) => (c.persona === view.current.persona || c.persona === "all") && (c.lang === view.current.lang || c.lang === "all"),
        stateAt: (t) => {
          const { home: h, away: aw, period, clock } = scoreboardAt(p.events, t, p.meta.period_min ?? 45);
          // Stats follow the playhead; older packages without snapshots fall back to the full-time state.
          const fs = finalRef.current;
          const snap = snapshotAt(p.states, t);
          const base = p.states?.length ? (snap && { ...snap, id: p.matchId }) : fs && fs.id === p.matchId ? fs : null;
          return base
            ? { ...base, home: { ...base.home, goals: h }, away: { ...base.away, goals: aw }, displayClock: clock, period, meta: p.meta }
            : { id: p.matchId, clockS: t, displayClock: clock, period, ended: false, home: team(p.meta, "home", h), away: team(p.meta, "away", aw),
                possessionHome: 0.5, momentum: 0, controlChaos: 0, topSpeeds: [], meta: p.meta };
        },
      };
    }).catch((e) => console.warn("replay load failed", e));
    return () => { stopped = true; feed.current = null; };
  }, [matchId]);

  return usePlayer(feed, matchId, false, 4);
}
