// The live feed's connection, kept out of the React hook so it can be unit tested with a fake socket.

import type { Cue } from "./types";

/** Seconds to wait before each reconnect attempt; the last value repeats. */
export const RECONNECT_BACKOFF_S = [1, 2, 5, 15];

/** The part of the browser WebSocket we use. */
export type SocketLike = Pick<WebSocket, "onopen" | "onclose" | "onmessage" | "close">;

export interface FeedDeps {
  /** A fresh Web PubSub URL: negotiated again on every (re)connect, since tokens expire. */
  negotiate: () => Promise<{ url: string }>;
  /** Cue history over REST, merged by id after every (re)connect so cues sent while we were away still show. */
  cues: () => Promise<{ cues: Cue[] }>;
  open: (url: string) => SocketLike;
  wait?: (ms: number, fn: () => void) => () => void;
}

export interface FeedHandlers {
  onMessage: (data: unknown) => void;
  onCues: (cues: Cue[]) => void;
  onConnected: (connected: boolean) => void;
}

const defaultWait = (ms: number, fn: () => void) => { const t = setTimeout(fn, ms); return () => clearTimeout(t); };

/** Keep a socket open until the returned stop function is called, reconnecting with backoff. */
export function keepConnected(deps: FeedDeps, h: FeedHandlers): () => void {
  const wait = deps.wait ?? defaultWait;
  let stopped = false;
  let attempt = 0;
  let socket: SocketLike | null = null;
  let cancelRetry: (() => void) | null = null;

  const retry = () => {
    if (stopped || cancelRetry) return;
    const delay = RECONNECT_BACKOFF_S[Math.min(attempt, RECONNECT_BACKOFF_S.length - 1)];
    attempt++;
    cancelRetry = wait(delay * 1000, () => { cancelRetry = null; void connect(); });
  };

  const connect = async () => {
    try {
      const { url } = await deps.negotiate();
      if (stopped) return;
      const s = deps.open(url);
      socket = s;
      s.onopen = () => {
        attempt = 0;
        h.onConnected(true);
        deps.cues().then((r) => { if (!stopped) h.onCues(r.cues); }).catch(() => {});
      };
      s.onclose = () => {
        if (socket === s) socket = null;
        h.onConnected(false);
        retry();
      };
      s.onmessage = (ev) => h.onMessage(ev.data);
    } catch (e) {
      console.warn("live connect failed", e);
      retry();
    }
  };

  void connect();
  return () => {
    stopped = true;
    cancelRetry?.();
    socket?.close();
  };
}
