import { useEffect, useRef } from "react";
import { API, api } from "./api";
import type { Timeline } from "./playback";
import { type Feed, type Live, usePlayer } from "./player";
import { lastAtOrBefore, mergeByTime } from "./sample";
import { keepConnected } from "./socket";
import type { Cue, Frame, Lang, MatchMeta, MatchState, Persona } from "./types";

/** Broadcast delay in wall-clock seconds: the player shows the match this far behind live, so
 * explanations written by agents (a few seconds of latency) land exactly on the play they explain. */
export const BROADCAST_DELAY_S = 10;

/** Cues to load on joining: the API's most, enough for every cue of a whole match, so scrubbing back keeps its explanations. */
const CUE_HISTORY = 1000;

/** The simulator's position as last heard: at wall time `wall` it was at `matchT`, moving at `speed`. */
interface Clock { matchT: number; wall: number; speed: number }
interface Heard { wall: number; t: number }

/** One broadcast tick, as sent on the socket and kept in the match's recording. `wallMs` is the
 *  server's clock when it was sent; recordings made before it existed lack it. */
export interface Tick { type: "tick"; frames?: Frame[]; state?: MatchState | null; wallMs?: number }

interface LiveFeed extends Feed {
  states: MatchState[];
  meta: MatchMeta | null;
  clock: Clock | null;
  /** Recent socket ticks: their slope is the simulator's pace. */
  heard: Heard[];
  /** Pace read from the recording, used until the socket has been heard long enough to measure it. */
  paceHint: number;
  byId: Map<string, Cue>;
  /** Recording loads asked for (one on joining, one more each time the socket comes back after a
   *  drop), how many of those the last successful load covered, and the bytes of the recording
   *  loaded so far: later loads ask only for what follows. */
  recording: { wanted: number; loaded: number; size: number };
}

const byT = (f: Frame) => f.t_s;
const byClock = (s: MatchState) => s.clockS;
const wallNow = () => performance.now() / 1000;

export function liveFeed(): LiveFeed {
  const f: LiveFeed = {
    connected: false, frames: [], states: [], meta: null, clock: null, heard: [], paceHint: 1, byId: new Map(), late: [],
    recording: { wanted: 1, loaded: 0, size: 0 },
    get cues() { return f.byId.values(); },
    shows: () => true,
    timeline: (now) => timelineAt(f, now),
    stateAt: (t) => {
      const s = lastAtOrBefore(f.states, t + 0.5, byClock) ?? f.states[0];
      return s ? { ...s, meta: f.meta ?? s.meta } : null;
    },
  };
  return f;
}

/** From the first recorded frame to the live edge less the broadcast delay, never past the newest frame. */
function timelineAt(f: LiveFeed, now: number): Timeline | null {
  const fs = f.frames;
  if (!fs.length) return null;
  const start = fs[0].t_s, newest = fs[fs.length - 1].t_s, c = f.clock;
  const end = c ? c.matchT + (now - c.wall - BROADCAST_DELAY_S) * c.speed : Infinity;
  return end < newest ? { start, end: Math.max(start, end), live: true, rate: c!.speed } : { start, end: newest, live: true, rate: 0 };
}

const pace = (matchS: number, wallS: number) => Math.min(60, Math.max(0.5, matchS / wallS));

/** Pace is the slope over the last dozen or so seconds of ticks, not tick to tick: ticks arrive 1 to
 *  1.5 s apart carrying whatever frames piled up, so a single interval swings the estimate. */
function onFrames(f: LiveFeed, incoming: Frame[]) {
  if (!incoming.length) return;
  const newest = incoming[incoming.length - 1].t_s;
  const now = wallNow();
  if (!f.clock || newest > f.clock.matchT) {
    f.heard = [...f.heard.filter((h) => now - h.wall < 15), { wall: now, t: newest }];
    const first = f.heard[0];
    f.clock = { matchT: newest, wall: now, speed: now - first.wall >= 3 ? pace(newest - first.t, now - first.wall) : f.paceHint };
  }
  f.frames = mergeByTime(f.frames, incoming, byT);
}

/** The simulator's pace over a recording's last ten intervals between ticks, from their server clocks
 *  (older recordings lack them, and their ticks are taken as a second apart), and the newest match time
 *  recorded. Fewer intervals, such as a few seconds' tail, swing with when each tick happened to be
 *  sent, so they give null. A tick counts only if it moves past every match time recorded before it:
 *  after an interpreter restart the replayed ticks split the frames at other times, and a tick whose
 *  append lost a race to a later one's is one fewer to measure, not a step back. */
export function recordedPace(ticks: Tick[]): { pace: number; newest: number } | null {
  const ends: { t: number; wallMs?: number }[] = [];
  for (const { frames, wallMs } of ticks) {
    const t = frames?.length ? frames[frames.length - 1].t_s : -Infinity;
    if (t > (ends.at(-1)?.t ?? -Infinity)) ends.push({ t, wallMs });
  }
  const k = 10;
  if (ends.length <= k) return null;
  const a = ends[ends.length - 1 - k], b = ends[ends.length - 1];
  const wallS = a.wallMs !== undefined && b.wallMs !== undefined && b.wallMs > a.wallMs ? (b.wallMs - a.wallMs) / 1000 : k;
  return { pace: pace(b.t - a.t, wallS), newest: b.t };
}

/** Everything broadcast before this viewer joined, or since the last load, and whether it loaded. The
 *  tail also gives a first estimate of the simulator's pace from the ticks' server clocks, sparing the
 *  socket estimate a slow warm-up. */
async function loadRecording(f: LiveFeed, matchId: string, signal: AbortSignal): Promise<boolean> {
  try {
    const from = f.recording.size;
    const r = await fetch(`${API}/api/matches/${matchId}/recording${from ? `?from=${from}` : ""}`, { signal });
    if (r.status === 400 && from) {  // an offset past the end isn't from this recording: read it whole
      f.recording.size = 0;
      return await loadRecording(f, matchId, signal);
    }
    if (!r.ok) throw new Error(`${r.status}`);
    const body = await r.arrayBuffer();  // already inflated: its length is bytes of the recording
    const ticks: Tick[] = new TextDecoder().decode(body).split("\n").filter(Boolean).map((line) => JSON.parse(line));
    if (!from && !ticks.length) return false;  // created before its first tick: try again
    f.recording.size = from + body.byteLength;
    const frames = ticks.flatMap((t) => t.frames ?? []).sort((a, b) => a.t_s - b.t_s);
    f.frames = mergeByTime(f.frames, frames, byT);
    f.states = mergeByTime(f.states, ticks.flatMap((t) => (t.state ? [t.state] : [])).sort((a, b) => a.clockS - b.clockS), byClock);
    const recorded = recordedPace(ticks);
    if (recorded && !f.states[f.states.length - 1]?.ended) {
      f.paceHint = recorded.pace;
      if (!f.clock) f.clock = { matchT: recorded.newest, wall: wallNow(), speed: f.paceHint };
      else if (f.heard.length < 2 || f.clock.wall - f.heard[0].wall < 3) f.clock = { ...f.clock, speed: f.paceHint };
    }
    return true;
  } catch (e) {
    if (!signal.aborted) console.warn("recording load failed", e);
    return false;
  }
}

/** Seconds to wait after each failed recording load. At kick-off a 404 is normal: nothing is recorded yet. */
const RECORDING_RETRY_S = [2, 5, 10, 20];
/** Seconds after a backfill to look once more: the interpreter appends each tick in the background, so
 *  ticks sent just before a reconnect may not be in the recording yet when the backfill reads it. */
const RECHECK_S = 5;
/** Seconds between tail loads while connected. Ticks from the socket don't say where they sit in the
 *  recording, so without these a reconnect would ask for everything since the viewer joined. */
const KEEP_UP_S = 60;
const sleep = (s: number) => new Promise((resolve) => setTimeout(resolve, s * 1000));

/** Loads the recording once the socket is open (whatever airs while it downloads then arrives on the
 *  socket), and what was recorded since after each reconnect, since ticks sent while the socket was
 *  down only exist in the recording. While connected it loads the tail every minute too, so that
 *  reconnect asks for a minute or so of ticks. Retries with back-off while the match is live, until `signal` aborts, which also
 *  cancels a download in progress. */
export async function keepLoadingRecording(f: LiveFeed, matchId: string, signal: AbortSignal) {
  let recheckAt = Infinity;
  for (let failures = 0; !signal.aborted;) {
    const wanted = f.recording.wanted;
    if (!f.connected || (f.recording.loaded === wanted && Date.now() < recheckAt)) { await sleep(0.2); continue; }
    const backfill = f.recording.size > 0 && f.recording.loaded !== wanted;
    const loaded = await loadRecording(f, matchId, signal);
    if (f.states[f.states.length - 1]?.ended) return;
    if (loaded) {
      f.recording.loaded = wanted;
      failures = 0;
      recheckAt = Date.now() + (backfill ? RECHECK_S : f.recording.size ? KEEP_UP_S : Infinity) * 1000;
    } else await sleep(RECORDING_RETRY_S[Math.min(failures++, RECORDING_RETRY_S.length - 1)]);
  }
}

function onMessage(f: LiveFeed, data: unknown) {
  try {
    let msg = JSON.parse(typeof data === "string" ? data : "");
    if (typeof msg === "string") msg = JSON.parse(msg); // tolerate double-encoded payloads
    if (msg.type === "tick") {
      onFrames(f, (msg.frames ?? []) as Frame[]);
      if (msg.state) f.states = mergeByTime(f.states, [msg.state as MatchState], byClock);
    } else if (msg.type === "frames") onFrames(f, msg.frames as Frame[]);
    else if (msg.type === "state") f.states = mergeByTime(f.states, [msg.state as MatchState], byClock);
    else if (msg.type === "cue") { f.byId.set(msg.cue.id, msg.cue as Cue); f.late.push(msg.cue as Cue); }
  } catch { /* ignore non-JSON */ }
}

/** One viewer's live connection: a socket kept open across drops, with their cue history merged once
 *  it opens and the cues each reopen missed merged as late ones. Returns its stop. Nothing from a
 *  stopped connection (a late history fetch, a closing socket's last messages) reaches the feed, which
 *  by then belongs to the viewer or language picked next. */
export function connect(f: LiveFeed, matchId: string, persona: Persona, lang: Lang): () => void {
  let stopped = false;
  let opens = 0;
  f.byId = new Map();
  f.late = [];
  const stopSocket = keepConnected({
    negotiate: () => api.negotiate(matchId, persona, lang),
    cues: () => api.cues(matchId, persona, lang, CUE_HISTORY),
    open: (url) => new WebSocket(url),
  }, {
    onMessage: (data) => { if (!stopped) onMessage(f, data); },
    // A late cue aired while the socket was down, like one the socket brings after its moment
    onCues: (cues, late) => {
      if (stopped) return;
      for (const c of cues) if (!f.byId.has(c.id)) { f.byId.set(c.id, c); if (late) f.late.push(c); }
    },
    onConnected: (up) => {
      if (stopped) return;
      f.connected = up;
      // A reopen, or a first open replacing another viewer's or language's socket, follows a gap only the recording has
      if (up && (opens++ > 0 || f.frames.length)) f.recording.wanted++;
    },
  });
  return () => { stopped = true; stopSocket(); f.connected = false; };
}

export function useLiveMatch(matchId: string | null, persona: Persona, lang: Lang): Live {
  const feed = useRef<LiveFeed | null>(null);

  useEffect(() => {  // frames, states and the recording outlive a switch of viewer or language
    if (!matchId) return;
    const f = liveFeed();
    feed.current = f;
    const stop = new AbortController();
    api.match(matchId).then((st) => {
      f.meta = st.meta ?? null;
      f.states = mergeByTime(f.states, [st], byClock);
    }).catch(() => {});
    void keepLoadingRecording(f, matchId, stop.signal);
    return () => { stop.abort(); if (feed.current === f) feed.current = null; };
  }, [matchId]);

  useEffect(() => {
    const f = feed.current;
    return matchId && f ? connect(f, matchId, persona, lang) : undefined;
  }, [matchId, persona, lang]);

  return usePlayer(feed, matchId, true, 1);
}
