import type { Frame } from "./types";

/** Index of the first frame strictly after t (binary search; frames are sorted by t_s). */
function after(fs: Frame[], t: number): number {
  let lo = 0, hi = fs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (fs[mid].t_s > t) hi = mid; else lo = mid + 1;
  }
  return lo;
}

const cr = (p0: number, p1: number, p2: number, p3: number, k: number) =>
  0.5 * (2 * p1 + (-p0 + p2) * k + (2 * p0 - 5 * p1 + 4 * p2 - p3) * k * k + (-p0 + 3 * p1 - 3 * p2 + p3) * k * k * k);

/**
 * Frame at time t, for smooth rendering between sparse feed samples. Players follow a
 * Catmull-Rom curve through the neighbouring samples (people don't move in straight segments);
 * the ball is interpolated linearly, because it really does travel straight between touches.
 */
export function sampleFrames(fs: Frame[], t: number): Frame | null {
  if (!fs.length) return null;
  const i = after(fs, t);
  if (i === 0) return fs[0];
  if (i >= fs.length) return fs[fs.length - 1];
  const a = fs[i - 1], b = fs[i];
  const z = fs[Math.max(0, i - 2)], c = fs[Math.min(fs.length - 1, i + 1)];
  const span = b.t_s - a.t_s;
  const k = span > 0 ? Math.min(1, Math.max(0, (t - a.t_s) / span)) : 0;
  const pz = new Map(z.players.map((p) => [p.id, p]));
  const pb = new Map(b.players.map((p) => [p.id, p]));
  const pc = new Map(c.players.map((p) => [p.id, p]));
  return {
    t_s: t,
    ball: [a.ball[0] + (b.ball[0] - a.ball[0]) * k, a.ball[1] + (b.ball[1] - a.ball[1]) * k, a.ball[2] + (b.ball[2] - a.ball[2]) * k],
    players: a.players.map((p1) => {
      const p2 = pb.get(p1.id);
      if (!p2) return p1;
      const p0 = pz.get(p1.id) ?? p1, p3 = pc.get(p1.id) ?? p2;
      // a jump (substitution, half-time reset) is not something to smooth through
      if (Math.abs(p2.x - p1.x) + Math.abs(p2.y - p1.y) > 8) return k < 0.5 ? p1 : p2;
      return { id: p1.id, x: cr(p0.x, p1.x, p2.x, p3.x, k), y: cr(p0.y, p1.y, p2.y, p3.y, k) };
    }),
  };
}
