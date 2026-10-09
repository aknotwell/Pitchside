import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { sampleFrames } from "./sample";
import type { Cue, Frame, Lang, MatchState, Persona } from "./types";

/** Broadcast delay in wall-clock seconds: the player shows the match this far behind live, so
 * explanations written by agents (a few seconds of latency) land exactly on the play they explain. */
export const BROADCAST_DELAY_S = 10;

interface Clock { matchT: number; wall: number; speed: number }

export interface Live {
  connected: boolean;
  playhead: number;
  liveEdge: number;
  frame: Frame | null;
  state: MatchState | null;
  cues: Cue[];
  history: Cue[];
  speed: number;
  /** Frame at this exact instant (for 60 fps renderers); null until the feed starts. */
  sample: () => Frame | null;
}

export function useLiveMatch(matchId: string | null, persona: Persona, lang: Lang): Live {
  const frames = useRef<Frame[]>([]);
  const states = useRef<MatchState[]>([]);
  const allCues = useRef<Map<string, Cue>>(new Map());
  const shownAt = useRef<Map<string, number>>(new Map());
  const clock = useRef<Clock | null>(null);
  const sampler = useRef<() => Frame | null>(() => null);
  const sample = useRef(() => sampler.current()).current;
  const [live, setLive] = useState<Live>({ connected: false, playhead: 0, liveEdge: 0, frame: null, state: null, cues: [], history: [], speed: 1, sample });

  useEffect(() => {
    if (!matchId) return;
    let ws: WebSocket | null = null;
    let stopped = false;
    frames.current = [];
    states.current = [];
    allCues.current = new Map();
    shownAt.current = new Map();
    clock.current = null;

    const onFrames = (incoming: Frame[]) => {
      if (!incoming.length) return;
      const last = incoming[incoming.length - 1];
      const now = performance.now() / 1000;
      const c = clock.current;
      if (c && last.t_s > c.matchT) {
        const inst = (last.t_s - c.matchT) / Math.max(0.2, now - c.wall);
        const speed = Math.min(60, Math.max(0.5, 0.8 * c.speed + 0.2 * inst));
        clock.current = { matchT: last.t_s, wall: now, speed };
      } else if (!c) {
        clock.current = { matchT: last.t_s, wall: now, speed: 1 };
      }
      frames.current.push(...incoming);
      const cutoff = last.t_s - 600;
      if (frames.current.length > 4000) frames.current = frames.current.filter((f) => f.t_s >= cutoff);
    };

    (async () => {
      try {
        const [st, hist] = await Promise.all([api.match(matchId).catch(() => null), api.cues(matchId, persona, lang).catch(() => ({ cues: [] }))]);
        if (st) states.current.push(st);
        for (const c of hist.cues) {
          allCues.current.set(c.id, c);
          shownAt.current.set(c.id, -1); // history: never pops up again
        }
        const { url } = await api.negotiate(matchId, persona, lang);
        if (stopped) return;
        ws = new WebSocket(url);
        ws.onopen = () => setLive((l) => ({ ...l, connected: true }));
        ws.onclose = () => setLive((l) => ({ ...l, connected: false }));
        ws.onmessage = (ev) => {
          try {
            let msg = JSON.parse(typeof ev.data === "string" ? ev.data : "");
            if (typeof msg === "string") msg = JSON.parse(msg); // tolerate double-encoded payloads
            if (msg.type === "tick") {
              onFrames((msg.frames ?? []) as Frame[]);
              if (msg.state) states.current.push(msg.state as MatchState);
            } else if (msg.type === "frames") onFrames(msg.frames as Frame[]);
            else if (msg.type === "state") states.current.push(msg.state as MatchState);
            else if (msg.type === "cue") allCues.current.set(msg.cue.id, msg.cue as Cue);
          } catch { /* ignore non-JSON */ }
        };
      } catch (e) {
        console.warn("live connect failed", e);
      }
    })();

    const playheadAt = (now: number) => {
      const c = clock.current, fs = frames.current;
      if (!c || !fs.length) return null;
      const liveEdge = c.matchT + (now - c.wall) * c.speed;
      return { liveEdge, playhead: Math.max(fs[0].t_s, liveEdge - BROADCAST_DELAY_S * c.speed) };
    };
    sampler.current = () => {
      const ph = playheadAt(performance.now() / 1000);
      return ph ? sampleFrames(frames.current, ph.playhead) : null;
    };

    let raf = 0;
    let lastPaint = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const nowMs = performance.now();
      if (nowMs - lastPaint < 66) return; // ~15 fps is plenty for a 2 Hz feed
      lastPaint = nowMs;
      const now = nowMs / 1000;
      const c = clock.current;
      const fs = frames.current;
      let frame: Frame | null = null;
      let playhead = 0;
      let liveEdge = 0;
      const ph = playheadAt(now);
      if (ph) {
        ({ liveEdge, playhead } = ph);
        frame = sampleFrames(fs, playhead);
      }
      // State: latest doc at or before the playhead (or the newest if we have no frames yet)
      const sts = states.current;
      let state: MatchState | null = sts.length ? sts[sts.length - 1] : null;
      if (frame) {
        for (let k = sts.length - 1; k >= 0; k--) {
          if (sts[k].clockS <= playhead + 0.5) { state = { ...sts[k], meta: sts[0].meta ?? sts[k].meta }; break; }
        }
        if (state && !state.meta && sts[0]?.meta) state = { ...state, meta: sts[0].meta };
      }
      // Cues: show once the playhead reaches display_at_s, for ttl wall seconds
      const active: Cue[] = [];
      const history: Cue[] = [];
      for (const cue of allCues.current.values()) {
        const shown = shownAt.current.get(cue.id);
        if (shown === undefined && frame && cue.display_at_s <= playhead) shownAt.current.set(cue.id, now);
        const s = shownAt.current.get(cue.id);
        if (s !== undefined) {
          history.push(cue);
          if (s > 0 && now - s < cue.ttl_s) active.push(cue);
        }
      }
      history.sort((a, b) => b.display_at_s - a.display_at_s);
      active.sort((a, b) => a.priority - b.priority);
      setLive((l) => ({ ...l, playhead, liveEdge, frame, state, cues: active, history: history.slice(0, 40), speed: c?.speed ?? 1 }));
    };
    raf = requestAnimationFrame(tick);
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      sampler.current = () => null;
      ws?.close();
    };
  }, [matchId, persona, lang]);

  return live;
}
