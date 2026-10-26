// The live feed's connection, kept out of the React hook so it can be unit tested with a fake socket.

import type { Cue } from "./types";

/** Seconds to wait before each reconnect attempt; the last value repeats. */
export const RECONNECT_BACKOFF_S = [1, 2, 5, 15];

/** The part of the browser WebSocket we use. */
export type SocketLike = Pick<WebSocket, "onopen" | "onclose" | "onmessage" | "close">;

export interface FeedDeps {
  /** A fresh Web PubSub URL: negotiated again on every (re)connect, since tokens expire. */
  negotiate: () => Promise<{ url: string }>;
  /** Cues over REST, fetched on every open: the first open's are the viewer's history, and a reopen's
   *  hold the cues sent while the socket was down. */
  cues: () => Promise<{ cues: Cue[] }>;
  open: (url: string) => SocketLike;
  wait?: (ms: number, fn: () => void) => () => void;
}

export interface FeedHandlers {
  onMessage: (data: unknown) => void;
  /** One open's cues. The first open's are the history, not `late`: retried until they land, and merged
   *  before any later open's whatever order the replies come back in. A later open's are `late`. */
  onCues: (cues: Cue[], late: boolean) => void;
  onConnected: (connected: boolean) => void;
}

const defaultWait = (ms: number, fn: () => void) => { const t = setTimeout(fn, ms); return () => clearTimeout(t); };
const backoffMs = (failures: number) => RECONNECT_BACKOFF_S[Math.min(failures, RECONNECT_BACKOFF_S.length - 1)] * 1000;

/** Keep a socket open until the returned stop function is called, reconnecting with backoff. */
export function keepConnected(deps: FeedDeps, h: FeedHandlers): () => void {
  const wait = deps.wait ?? defaultWait;
  let stopped = false;
  let attempt = 0;
  let socket: SocketLike | null = null;
  let cancelRetry: (() => void) | null = null;
  let history: Promise<void> | null = null;
  let cancelHistoryRetry: (() => void) | null = null;

  const retry = () => {
    if (stopped || cancelRetry) return;
    cancelRetry = wait(backoffMs(attempt++), () => { cancelRetry = null; void connect(); });
  };

  const fetchHistory = (failures: number): Promise<void> => deps.cues().then(
    (r) => { if (!stopped) h.onCues(r.cues, false); },
    (e) => new Promise<void>((resolve) => {
      if (stopped) return;
      console.warn("cue history failed", e);
      cancelHistoryRetry = wait(backoffMs(failures), () => resolve(fetchHistory(failures + 1)));
    }));

  const connect = async () => {
    try {
      const { url } = await deps.negotiate();
      if (stopped) return;
      const s = deps.open(url);
      socket = s;
      s.onopen = () => {
        attempt = 0;
        h.onConnected(true);
        if (!history) { history = fetchHistory(0); return; }
        const missed = deps.cues().catch(() => null);
        void history.then(() => missed).then((r) => { if (r && !stopped) h.onCues(r.cues, true); });
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
    cancelHistoryRetry?.();
    socket?.close();
  };
}
