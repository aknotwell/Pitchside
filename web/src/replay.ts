import { useEffect, useRef, useState } from "react";
import { API } from "./api";
import type { Live } from "./live";
import { sampleFrames } from "./sample";
import type { Cue, Frame, Lang, MatchMeta, MatchState, Persona, TeamSummary } from "./types";

interface ReplayPackage {
  matchId: string;
  meta: MatchMeta;
  playerIds: string[];
  frames: [number, number, number, ([number, number] | null)[]][];
  events: { event_id: string; clock_s: number; display_clock: string; type: string; team_id: string | null; outcome: string | null; period: number }[];
  cues: Cue[];
}

function team(meta: MatchMeta, side: "home" | "away", goals: number): TeamSummary {
  const t = meta[side];
  return { id: t.id, name: t.name, goals, shots: 0, shotsOnTarget: 0, xg: 0, passes: 0, passAccuracy: 0, finalThirdEntries: 0, ppda: null, pressures: 0, turnovers: 0 };
}

/** Plays a packaged replay with the same rendering contract as the live hook. */
export function useReplay(matchId: string | null, persona: Persona, lang: Lang, speed: number, finalState: MatchState | null): Live {
  const pkg = useRef<ReplayPackage | null>(null);
  const start = useRef<{ wall: number; t0: number }>({ wall: 0, t0: 0 });
  const shownAt = useRef<Map<string, number>>(new Map());
  const finalRef = useRef<MatchState | null>(finalState);
  finalRef.current = finalState;  // read inside the loop; changing it must not restart playback
  const frames = useRef<Frame[]>([]);
  const sampler = useRef<() => Frame | null>(() => null);
  const sample = useRef(() => sampler.current()).current;
  const [live, setLive] = useState<Live>({ connected: false, playhead: 0, liveEdge: 0, frame: null, state: null, cues: [], history: [], speed, sample });

  useEffect(() => {
    if (!matchId) return;
    let stopped = false;
    pkg.current = null;
    shownAt.current = new Map();
    fetch(`${API}/api/replays/${matchId}`).then((r) => r.json()).then((p: ReplayPackage) => {
      if (stopped) return;
      pkg.current = p;
      frames.current = p.frames.map(([t, bx, by, pl]) => ({
        t_s: t, ball: [bx, by, 0] as [number, number, number],
        players: p.playerIds.flatMap((id, j) => (pl[j] ? [{ id, x: pl[j]![0], y: pl[j]![1] }] : [])),
      }));
      start.current = { wall: performance.now() / 1000, t0: p.frames[0]?.[0] ?? 0 };
      setLive((l) => ({ ...l, connected: true }));
    }).catch((e) => console.warn("replay load failed", e));
    const playheadAt = (now: number) => {
      const p = pkg.current;
      if (!p || !p.frames.length) return null;
      const end = p.frames[p.frames.length - 1][0];
      let playhead = start.current.t0 + (now - start.current.wall) * speed;
      if (playhead > end + 20) { start.current = { wall: now, t0: p.frames[0][0] }; shownAt.current = new Map(); playhead = p.frames[0][0]; }
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
      const now = nowMs / 1000;
      const playhead = playheadAt(now)!;
      const frame = sampleFrames(frames.current, playhead)!;
      // scoreboard from events
      let h = 0, aw = 0, clock = "00:00", period = 1;
      for (const e of p.events) {
        if (e.clock_s > playhead) break;
        if (e.type === "shot" && e.outcome === "goal") { if (e.team_id === "h") h++; else aw++; }
        if (e.display_clock) clock = e.display_clock;
        period = e.period;
      }
      const fs = finalRef.current;
      const base = fs && fs.id === p.matchId ? fs : null;
      const state: MatchState = base
        ? { ...base, home: { ...base.home, goals: h }, away: { ...base.away, goals: aw }, displayClock: clock, period, meta: p.meta }
        : { id: p.matchId, clockS: playhead, displayClock: clock, period, ended: false, home: team(p.meta, "home", h), away: team(p.meta, "away", aw),
            possessionHome: 0.5, momentum: 0, controlChaos: 0, topSpeeds: [], meta: p.meta };
      const active: Cue[] = [], history: Cue[] = [];
      for (const c of p.cues) {
        if (!(c.persona === persona || c.persona === "all") || !(c.lang === lang || c.lang === "all")) continue;
        if (c.display_at_s > playhead) continue;
        if (!shownAt.current.has(c.id)) shownAt.current.set(c.id, now);
        history.push(c);
        if (now - (shownAt.current.get(c.id) ?? 0) < c.ttl_s) active.push(c);
      }
      history.sort((x, y) => y.display_at_s - x.display_at_s);
      active.sort((x, y) => x.priority - y.priority);
      setLive({ connected: true, playhead, liveEdge: playhead, frame, state, cues: active, history: history.slice(0, 40), speed, sample });
    };
    raf = requestAnimationFrame(tick);
    return () => { stopped = true; cancelAnimationFrame(raf); sampler.current = () => null; };
  }, [matchId, persona, lang, speed]);

  return live;
}
