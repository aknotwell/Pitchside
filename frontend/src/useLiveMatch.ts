import { useEffect, useRef, useState } from "react";
import type { LiveFrame, LiveMessage, MatchMeta, Score } from "./types";

export type Status = "connecting" | "live" | "full time" | "disconnected";

/** The two latest frames and when they arrived, so the pitch can interpolate. */
export interface FrameBuffer {
  prev: LiveFrame | null;
  cur: LiveFrame | null;
  arrivedAt: number;
  interval: number;
}

/** Opens /ws/match with the page's own query string (home, away, seed, speed, ...). */
export function useLiveMatch(query: string) {
  const [meta, setMeta] = useState<MatchMeta | null>(null);
  const [score, setScore] = useState<Score>({ home: 0, away: 0 });
  const [clock, setClock] = useState("00:00");
  const [status, setStatus] = useState<Status>("connecting");
  // Frames arrive 10 times a second; keep them out of React state to avoid re-renders.
  const frames = useRef<FrameBuffer>({ prev: null, cur: null, arrivedAt: 0, interval: 100 });

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/match${query}`);
    let ended = false;
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data) as LiveMessage;
      if (msg.type === "meta") {
        setMeta(msg.meta);
        setStatus("live");
      } else if (msg.type === "frame") {
        const now = performance.now();
        const f = frames.current;
        if (f.cur) f.interval = Math.min(Math.max(now - f.arrivedAt, 50), 250);
        frames.current = { ...f, prev: f.cur, cur: msg.frame, arrivedAt: now };
        setScore(msg.score);
        setClock(msg.frame.clock);
      } else {
        ended = true;
        setScore(msg.score);
        setStatus("full time");
      }
    };
    ws.onclose = () => !ended && setStatus("disconnected");
    return () => ws.close();
  }, [query]);

  return { meta, score, clock, status, frames };
}
