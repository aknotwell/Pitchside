import { useEffect, useRef } from "react";
import { API } from "./api";
import type { Live } from "./player";
import type { Cue, Lang } from "./types";

/** How a template is announced. Rank 0 cuts off a lower rank's call; a recap's audio is already on the cue. */
interface Rule { rank: number; recap?: true }
export const ANNOUNCED: Record<string, Rule> = {
  goal: { rank: 0 },
  "panel.recap": { rank: 0, recap: true },
  card: { rank: 1 },
  substitution: { rank: 2 },
};

/** Wall seconds a call may trail its cue going up, and wait in the queue, before it is dropped. */
export const MAX_LATE_S = 8;
export const MAX_QUEUED_S = 6;

/** One call to play. `key` is the moment it speaks for: every viewer and language variant shares it. */
export interface Call { key: string; url: string; rank: number; queuedAt: number }

const keyOf = (c: Cue) => c.moment_id ?? c.id;

/** Where a cue's call is fetched from, or null when it isn't announced. */
export function callUrl(c: Cue, lang: Lang): string | null {
  const rule = ANNOUNCED[c.template];
  if (!rule) return null;
  if (rule.recap) return typeof c.data.audio === "string" ? `${API}${c.data.audio}` : null;
  return `${API}/api/matches/${encodeURIComponent(c.matchId)}/cues/${encodeURIComponent(c.id)}/voice?lang=${lang}`;
}

/** Calls for the cues that went up since the last frame (`before` holds the moments up then), leaving
 *  out any that aired too long ago: a seek clears what is up without crossing anything, so it adds none. */
export function arrivals(before: ReadonlySet<string>, active: Cue[], lang: Lang, playhead: number, speed: number, now: number): Call[] {
  const calls: Call[] = [];
  for (const c of active) {
    const url = callUrl(c, lang), key = keyOf(c);
    if (!url || before.has(key) || calls.some((k) => k.key === key)) continue;
    if ((playhead - c.display_at_s) / Math.max(speed, 1) > MAX_LATE_S) continue;
    calls.push({ key, url, rank: ANNOUNCED[c.template].rank, queuedAt: now });
  }
  return calls;
}

export const momentsUp = (active: Cue[]) => new Set(active.map(keyOf));

/** The queue with `calls` added, best rank first, in arrival order within a rank. */
export const enqueue = (queue: Call[], calls: Call[]): Call[] => [...queue, ...calls].sort((a, b) => a.rank - b.rank);

/** The next call still worth playing and the queue left behind it; stale calls are dropped. */
export function next(queue: Call[], now: number): [Call | undefined, Call[]] {
  const fresh = queue.filter((c) => now - c.queuedAt <= MAX_QUEUED_S);
  return [fresh[0], fresh.slice(1)];
}

/** A goal or recap cuts off a card or substitution call; nothing cuts off another of its own rank. */
export const preempts = (playing: Call | null, incoming: Call[]) => !!playing && incoming.some((c) => c.rank === 0 && playing.rank > 0);

const SILENCE = "data:audio/wav;base64,UklGRnQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YVAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgA==";  // 10 ms, to unlock the element inside a click
const CLIPS_KEPT = 40;
/** A failed fetch is tried again after this long, not every frame while the server is down. */
const RETRY_MS = 3000;
const wallNow = () => performance.now() / 1000;

/**
 * Reads key moments aloud as they go up: fetches each call while its cue is still ahead of the
 * playhead, then plays it the frame the cue airs, one at a time. `source` names what is playing;
 * a new one forgets everything. Returns `unlock`, to call inside the click that turns sound on.
 */
export function useAnnouncer(live: Live, source: string | null, lang: Lang, on: boolean): () => void {
  const audio = useRef<HTMLAudioElement | null>(null);
  const s = useRef({ up: new Set<string>(), queue: [] as Call[], playing: null as Call | null, clips: new Map<string, Promise<string | null>>() }).current;
  const el = () => (audio.current ??= new Audio());

  const clip = (url: string) => {
    let p = s.clips.get(url);
    if (!p) {
      const q: Promise<string | null> = fetch(url).then((r) => (r.ok ? r.blob() : null)).then((b) => b && URL.createObjectURL(b)).catch(() => null);
      void q.then((u) => { if (!u) setTimeout(() => { if (s.clips.get(url) === q) s.clips.delete(url); }, RETRY_MS); });
      p = q;
      s.clips.set(url, p);
      for (const [old, q] of s.clips) {
        if (s.clips.size <= CLIPS_KEPT) break;
        s.clips.delete(old);
        void q.then((u) => u && URL.revokeObjectURL(u));
      }
    }
    return p;
  };
  const unlock = () => {
    if (s.playing) return;
    const a = el();
    a.src = SILENCE;
    a.play().catch(() => {});
  };
  const stop = () => { s.queue = []; s.playing = null; audio.current?.pause(); };
  const pump = async (): Promise<void> => {
    if (s.playing) return;
    const [call, rest] = next(s.queue, wallNow());
    s.queue = rest;
    if (!call) return;
    s.playing = call;
    const src = await clip(call.url);
    if (s.playing !== call) return;  // cut off or stopped while it loaded
    const done = () => { if (s.playing === call) { s.playing = null; void pump(); } };
    if (!src) return done();
    const a = el();
    a.src = src;
    a.onended = a.onerror = done;
    a.play().catch(done);
  };

  useEffect(() => () => {
    stop();
    for (const p of s.clips.values()) void p.then((u) => u && URL.revokeObjectURL(u));
    s.clips.clear();
    s.up = new Set();
  }, [source]);

  useEffect(() => { if (!on || live.paused) stop(); }, [on, live.paused]);

  useEffect(() => {  // sound left on from a last visit needs this page's first gesture before it can play
    if (!on) return;
    window.addEventListener("pointerdown", unlock, { once: true });
    window.addEventListener("keydown", unlock, { once: true });
    return () => { window.removeEventListener("pointerdown", unlock); window.removeEventListener("keydown", unlock); };
  }, [on]);

  useEffect(() => {
    if (!on) return;
    for (const c of live.upcoming) {
      const url = callUrl(c, lang);
      if (url) void clip(url);
    }
  }, [on, live.upcoming, lang]);

  useEffect(() => {
    const calls = on && !live.paused ? arrivals(s.up, live.cues, lang, live.playhead, live.speed, wallNow()) : [];
    s.up = momentsUp(live.cues);
    if (!calls.length) return;
    if (preempts(s.playing, calls)) { s.playing = null; audio.current?.pause(); }
    s.queue = enqueue(s.queue, calls);
    void pump();
  }, [live.cues]);

  return unlock;
}
