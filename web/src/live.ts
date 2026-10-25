import { useEffect, useRef } from "react";
import { API, api } from "./api";
import type { Timeline } from "./playback";
import { type Feed, type Live, usePlayer } from "./player";
import { lastAtOrBefore, mergeByTime } from "./sample";
import type { Cue, Frame, Lang, MatchMeta, MatchState, Persona } from "./types";

/** Broadcast delay in wall-clock seconds: the player shows the match this far behind live, so
 * explanations written by agents (a few seconds of latency) land exactly on the play they explain. */
export const BROADCAST_DELAY_S = 10;

/** The simulator's position as last heard: at wall time `wall` it was at `matchT`, moving at `speed`. */
interface Clock { matchT: number; wall: number; speed: number }
interface Heard { wall: number; t: number }

/** One broadcast tick, as sent on the socket and kept in the match's recording. */
interface Tick { type: "tick"; frames?: Frame[]; state?: MatchState | null }

interface LiveFeed extends Feed {
  states: MatchState[];
  meta: MatchMeta | null;
  clock: Clock | null;
  /** Recent socket ticks: their slope is the simulator's pace. */
  heard: Heard[];
  /** Pace read from the recording, used until the socket has been heard long enough to measure it. */
  paceHint: number;
  byId: Map<string, Cue>;
  recording: "none" | "loading" | "loaded";
}

const byT = (f: Frame) => f.t_s;
const byClock = (s: MatchState) => s.clockS;
const wallNow = () => performance.now() / 1000;

function liveFeed(): LiveFeed {
  const f: LiveFeed = {
    connected: false, frames: [], states: [], meta: null, clock: null, heard: [], paceHint: 1, byId: new Map(), late: [], recording: "none",
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

/** Everything broadcast before this viewer joined. Ticks are about a second of wall time apart, so the
 *  tail also gives a first estimate of the simulator's pace, sparing the socket estimate a slow warm-up. */
async function loadRecording(f: LiveFeed, matchId: string) {
  f.recording = "loading";
  try {
    const r = await fetch(`${API}/api/matches/${matchId}/recording`);
    if (!r.ok) throw new Error(`${r.status}`);
    const ticks: Tick[] = (await r.text()).split("\n").filter(Boolean).map((line) => JSON.parse(line));
    const frames = ticks.flatMap((t) => t.frames ?? []).sort((a, b) => a.t_s - b.t_s);
    f.frames = mergeByTime(f.frames, frames, byT);
    f.states = mergeByTime(f.states, ticks.flatMap((t) => (t.state ? [t.state] : [])).sort((a, b) => a.clockS - b.clockS), byClock);
    const ends = ticks.flatMap((t) => (t.frames?.length ? [t.frames[t.frames.length - 1].t_s] : []));
    const k = Math.min(10, ends.length - 1);
    if (k >= 2 && !f.states[f.states.length - 1]?.ended) {
      f.paceHint = pace(ends[ends.length - 1] - ends[ends.length - 1 - k], k);
      if (!f.clock) f.clock = { matchT: ends[ends.length - 1], wall: wallNow(), speed: f.paceHint };
      else if (f.heard.length < 2 || f.clock.wall - f.heard[0].wall < 3) f.clock = { ...f.clock, speed: f.paceHint };
    }
    f.recording = "loaded";
  } catch (e) {
    f.recording = "none";  // retried on the next socket open
    console.warn("recording load failed", e);
  }
}

export function useLiveMatch(matchId: string | null, persona: Persona, lang: Lang): Live {
  const feed = useRef<LiveFeed | null>(null);

  useEffect(() => {  // frames, states and the recording outlive a switch of viewer or language
    if (!matchId) return;
    const f = liveFeed();
    feed.current = f;
    api.match(matchId).then((st) => {
      f.meta = st.meta ?? null;
      f.states = mergeByTime(f.states, [st], byClock);
    }).catch(() => {});
    return () => { if (feed.current === f) feed.current = null; };
  }, [matchId]);

  useEffect(() => {
    const f = feed.current;
    if (!matchId || !f) return;
    let ws: WebSocket | null = null;
    let stopped = false;
    f.byId = new Map();
    f.late = [];
    (async () => {
      try {
        const hist = await api.cues(matchId, persona, lang).catch(() => ({ cues: [] as Cue[] }));
        for (const c of hist.cues) f.byId.set(c.id, c);
        const { url } = await api.negotiate(matchId, persona, lang);
        if (stopped) return;
        ws = new WebSocket(url);
        ws.onopen = () => {
          f.connected = true;
          // Socket first, then the recording: whatever airs while it downloads arrives on the socket
          if (f.recording === "none") void loadRecording(f, matchId);
        };
        ws.onclose = () => { f.connected = false; };
        ws.onmessage = (ev) => {
          try {
            let msg = JSON.parse(typeof ev.data === "string" ? ev.data : "");
            if (typeof msg === "string") msg = JSON.parse(msg); // tolerate double-encoded payloads
            if (msg.type === "tick") {
              onFrames(f, (msg.frames ?? []) as Frame[]);
              if (msg.state) f.states = mergeByTime(f.states, [msg.state as MatchState], byClock);
            } else if (msg.type === "frames") onFrames(f, msg.frames as Frame[]);
            else if (msg.type === "state") f.states = mergeByTime(f.states, [msg.state as MatchState], byClock);
            else if (msg.type === "cue") { f.byId.set(msg.cue.id, msg.cue as Cue); f.late.push(msg.cue as Cue); }
          } catch { /* ignore non-JSON */ }
        };
      } catch (e) {
        console.warn("live connect failed", e);
      }
    })();
    return () => { stopped = true; ws?.close(); f.connected = false; };
  }, [matchId, persona, lang]);

  return usePlayer(feed, matchId, true, 1);
}
