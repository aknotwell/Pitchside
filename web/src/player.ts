import { useEffect, useRef, useState, type RefObject } from "react";
import { crossed, goLive, positionAt, seek, setSpeed, settle, speedOf, type Timeline, togglePause, type Transport } from "./playback";
import { sampleFrames } from "./sample";
import type { Cue, Frame, MatchState } from "./types";

/** Everything a live or replay feed has loaded so far, read by the player on every frame. */
export interface Feed {
  connected: boolean;
  frames: Frame[];
  timeline: (now: number) => Timeline | null;
  stateAt: (playhead: number) => MatchState | null;
  cues: Iterable<Cue>;
  /** Which of `cues` this viewer sees (replays carry every viewer and language). */
  shows: (c: Cue) => boolean;
  /** Cues that arrived after the playhead had already passed them; drained by the player. */
  late: Cue[];
}

export interface Controls { togglePause: () => void; seek: (t: number) => void; setSpeed: (speed: number) => void; goLive: () => void }

export interface Live {
  connected: boolean;
  playhead: number;
  /** Match time the player can show; null until the first frame arrives. */
  timeline: Timeline | null;
  paused: boolean;
  followLive: boolean;
  speed: number;
  frame: Frame | null;
  state: MatchState | null;
  /** Clock shown at the timeline's end. */
  endClock: string | null;
  cues: Cue[];
  history: Cue[];
  /** Frame at this exact instant (for 60 fps renderers); null until the feed starts. */
  sample: () => Frame | null;
  controls: Controls;
}

const wallNow = () => performance.now() / 1000;

/**
 * Plays a feed through the viewer's transport. `key` names what is playing; a new key starts over,
 * live ones at the live edge, replays from kick-off, both at the speed the viewer last picked.
 */
export function usePlayer(feed: RefObject<Feed | null>, key: string | null, live: boolean, initialSpeed: number): Live {
  const tr = useRef<Transport>({ anchor: { wall: 0, t0: 0, speed: initialSpeed }, paused: false, followLive: live });
  const tl = useRef<Timeline | null>(null);
  const jumped = useRef(true);  // the next frame crosses no cues
  const [controls] = useState<Controls>(() => {
    const run = (f: (t: Transport, now: number, l: Timeline) => Transport, jump = false) => {
      if (!tl.current) return;
      tr.current = f(tr.current, wallNow(), tl.current);
      if (jump) jumped.current = true;
    };
    return {
      togglePause: () => run(togglePause),
      seek: (t) => run((a, now, l) => seek(a, now, l, t), true),
      setSpeed: (speed) => run((a, now, l) => setSpeed(a, now, l, speed)),
      goLive: () => run(goLive, true),
    };
  });
  const sampler = useRef<() => Frame | null>(() => null);
  const sample = useRef(() => sampler.current()).current;
  const empty: Live = { connected: false, playhead: 0, timeline: null, paused: false, followLive: live, speed: initialSpeed, frame: null,
                        state: null, endClock: null, cues: [], history: [], sample, controls };
  const [view, setView] = useState<Live>(empty);

  useEffect(() => {
    if (!key) return;
    tr.current = { anchor: { wall: 0, t0: 0, speed: tr.current.anchor.speed }, paused: false, followLive: live };
    tl.current = null;
    let started = false;
    jumped.current = true;
    const shownAt = new Map<string, number>();
    let prev: number | null = null;
    let cueClock = 0;  // wall seconds of playback: cue lifetimes stand still while paused
    let last = 0;

    sampler.current = () => {  // runs at the renderer's frame rate, so it only reads
      const now = wallNow(), f = feed.current, l = f?.timeline(now);
      return f && l && started ? sampleFrames(f.frames, positionAt(tr.current, now, l)) : null;
    };

    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const nowMs = performance.now();
      if (nowMs - last < 66) return;  // ~15 fps is plenty for a 5 Hz feed
      if (nowMs - last > 1000) jumped.current = true;  // back from a hidden tab: don't pop everything missed
      if (!tr.current.paused && last) cueClock += (nowMs - last) / 1000;
      last = nowMs;
      const now = nowMs / 1000;
      const f = feed.current;
      if (!f) return;
      const l = f.timeline(now);
      tl.current = l;
      if (!l) {
        setView({ ...empty, connected: f.connected, state: f.stateAt(Infinity) });
        return;
      }
      if (!started) { tr.current = { ...tr.current, anchor: { wall: now, t0: l.start, speed: tr.current.anchor.speed } }; started = true; }
      tr.current = settle(tr.current, now, l);
      const playhead = positionAt(tr.current, now, l);
      const t = tr.current;
      if (jumped.current) { prev = null; jumped.current = false; shownAt.clear(); }  // what was up belongs to the moment left behind
      for (const c of crossed(f.cues, prev, playhead)) shownAt.set(c.id, cueClock);
      // A cue that only arrived once its moment had aired still pops up for a viewer watching live
      for (const c of f.late.splice(0)) if (t.followLive && !t.paused && c.display_at_s <= playhead) shownAt.set(c.id, cueClock);
      prev = playhead;
      const active: Cue[] = [], history: Cue[] = [];
      for (const c of f.cues) {
        if (c.display_at_s > playhead || !f.shows(c)) continue;
        history.push(c);
        const s = shownAt.get(c.id);
        if (s !== undefined && cueClock - s < c.ttl_s) active.push(c);
      }
      history.sort((a, b) => b.display_at_s - a.display_at_s);
      active.sort((a, b) => a.priority - b.priority);
      setView({
        connected: f.connected, playhead, timeline: l, paused: t.paused, followLive: l.live && t.followLive, speed: speedOf(t, l),
        frame: sampleFrames(f.frames, playhead), state: f.stateAt(playhead), endClock: f.stateAt(l.end)?.displayClock ?? null,
        cues: active, history: history.slice(0, 40), sample, controls,
      });
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); sampler.current = () => null; tl.current = null; };
  }, [key, live]);

  return view;
}
