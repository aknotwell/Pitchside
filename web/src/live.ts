import { useEffect, useRef, useState } from "react";
import { api } from "./api";
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
}

function lerpFrame(a: Frame, b: Frame, t: number): Frame {
  const span = b.t_s - a.t_s;
  const k = span > 0 ? Math.min(1, Math.max(0, (t - a.t_s) / span)) : 0;
  const pb = new Map(b.players.map((p) => [p.id, p]));
  return {
    t_s: t,
    ball: [a.ball[0] + (b.ball[0] - a.ball[0]) * k, a.ball[1] + (b.ball[1] - a.ball[1]) * k, a.ball[2] + (b.ball[2] - a.ball[2]) * k],
    players: a.players.map((p) => {
      const q = pb.get(p.id);
      return q ? { id: p.id, x: p.x + (q.x - p.x) * k, y: p.y + (q.y - p.y) * k } : p;
    }),
  };
}

export function useLiveMatch(matchId: string | null, persona: Persona, lang: Lang): Live {
  const frames = useRef<Frame[]>([]);
  const states = useRef<MatchState[]>([]);
  const allCues = useRef<Map<string, Cue>>(new Map());
  const shownAt = useRef<Map<string, number>>(new Map());
  const clock = useRef<Clock | null>(null);
  const [live, setLive] = useState<Live>({ connected: false, playhead: 0, liveEdge: 0, frame: null, state: null, cues: [], history: [], speed: 1 });

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
            if (msg.type === "frames") onFrames(msg.frames as Frame[]);
            else if (msg.type === "state") states.current.push(msg.state as MatchState);
            else if (msg.type === "cue") allCues.current.set(msg.cue.id, msg.cue as Cue);
          } catch { /* ignore non-JSON */ }
        };
      } catch (e) {
        console.warn("live connect failed", e);
      }
    })();

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
      if (c && fs.length) {
        liveEdge = c.matchT + (now - c.wall) * c.speed;
        playhead = Math.max(fs[0].t_s, liveEdge - BROADCAST_DELAY_S * c.speed);
        let i = fs.findIndex((f) => f.t_s > playhead);
        if (i === -1) i = fs.length - 1;
        frame = i > 0 ? lerpFrame(fs[i - 1], fs[i], playhead) : fs[0];
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
      ws?.close();
    };
  }, [matchId, persona, lang]);

  return live;
}
