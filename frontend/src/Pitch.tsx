import { useEffect, useRef, type RefObject } from "react";
import type { FrameBuffer } from "./useLiveMatch";
import type { LiveFrame, MatchMeta } from "./types";

const L = 105; // pitch length and width in metres, as in schemas.py
const W = 68;
const M = 4; // margin around the pitch (m)
const GRASS = ["#3d7a3a", "#367035"];
const PLAYER_R = 1.2;

export type Kit = { fill: string; edge: string };

function rgb(hex: string): number[] {
  const n = parseInt(hex.slice(1), 16);
  return [n >> 16, (n >> 8) & 255, n & 255];
}
const near = (a: string, b: string) =>
  Math.hypot(...rgb(a).map((v, i) => v - rgb(b)[i])) < 110;

/** Primary colours, unless a kit would blend into the grass or the other team. */
export function teamKits(meta: MatchMeta): { home: Kit; away: Kit } {
  const pick = ([a, b]: [string, string], avoid: string[]): Kit =>
    avoid.some((c) => near(a, c)) ? { fill: b, edge: a } : { fill: a, edge: b };
  const home = pick(meta.home.colors, [GRASS[0]]);
  return { home, away: pick(meta.away.colors, [GRASS[0], home.fill]) };
}

function kits(meta: MatchMeta): Record<string, Kit> {
  const { home, away } = teamKits(meta);
  const byPlayer: Record<string, Kit> = {};
  meta.home.players.forEach((p) => (byPlayer[p.id] = home));
  meta.away.players.forEach((p) => (byPlayer[p.id] = away));
  return byPlayer;
}

function drawPitch(ctx: CanvasRenderingContext2D) {
  for (let i = 0; i < 14; i++) {
    ctx.fillStyle = GRASS[i % 2];
    ctx.fillRect(-M + (i * (L + 2 * M)) / 14, -M, (L + 2 * M) / 14 + 0.05, W + 2 * M);
  }
  ctx.strokeStyle = "rgba(255,255,255,0.85)";
  ctx.lineWidth = 0.15;
  const box = (x: number, w: number, h: number) => ctx.strokeRect(x, (W - h) / 2, w, h);
  ctx.strokeRect(0, 0, L, W);
  ctx.beginPath();
  ctx.moveTo(L / 2, 0);
  ctx.lineTo(L / 2, W);
  ctx.moveTo(L / 2 + 9.15, W / 2);
  ctx.arc(L / 2, W / 2, 9.15, 0, 2 * Math.PI);
  ctx.stroke();
  for (const [x, dir] of [[0, 1], [L, -1]]) {
    box(dir > 0 ? x : x - 16.5, 16.5, 40.32); // penalty area
    box(dir > 0 ? x : x - 5.5, 5.5, 18.32); // goal area
    box(dir > 0 ? x - 1.5 : x, 1.5, 7.32); // goal
    const spot = x + dir * 11;
    ctx.beginPath(); // penalty arc: the part of the 9.15 m circle outside the box
    const a = Math.acos(5.5 / 9.15);
    ctx.arc(spot, W / 2, 9.15, dir > 0 ? -a : Math.PI - a, dir > 0 ? a : Math.PI + a);
    ctx.stroke();
  }
  ctx.fillStyle = "rgba(255,255,255,0.85)";
  for (const x of [11, L / 2, L - 11]) {
    ctx.beginPath();
    ctx.arc(x, W / 2, 0.25, 0, 2 * Math.PI);
    ctx.fill();
  }
}

/** Positions between the two latest frames, so 10 Hz data moves smoothly at 60 fps. */
function interpolate(buf: FrameBuffer, now: number): LiveFrame | null {
  const { prev, cur } = buf;
  if (!cur || !prev || prev.period !== cur.period) return cur;
  const a = Math.min((now - buf.arrivedAt) / buf.interval, 1);
  const lerp = (p: number, c: number) => p + (c - p) * a;
  const before = new Map(prev.players.map((p) => [p.id, p]));
  return {
    ...cur,
    ball: [lerp(prev.ball[0], cur.ball[0]), lerp(prev.ball[1], cur.ball[1]), lerp(prev.ball[2], cur.ball[2])],
    players: cur.players.map((p) => {
      const b = before.get(p.id);
      return b ? { ...p, x: lerp(b.x, p.x), y: lerp(b.y, p.y) } : p;
    }),
  };
}

export function Pitch({ meta, frames }: { meta: MatchMeta; frames: RefObject<FrameBuffer> }) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const kit = kits(meta);
    const numbers = new Map([...meta.home.players, ...meta.away.players].map((p) => [p.id, p.number]));
    let raf = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const el = canvas.current;
      const ctx = el?.getContext("2d");
      if (!el || !ctx) return;
      const dpr = window.devicePixelRatio || 1;
      const w = Math.round(el.clientWidth * dpr);
      const h = Math.round((w * (W + 2 * M)) / (L + 2 * M));
      if (el.width !== w || el.height !== h) Object.assign(el, { width: w, height: h });
      const s = w / (L + 2 * M);
      // Pitch y points up (origin bottom-left), canvas y points down: flip it.
      ctx.setTransform(s, 0, 0, -s, M * s, (W + M) * s);
      drawPitch(ctx);

      const f = interpolate(frames.current, performance.now());
      if (!f) return;
      for (const p of f.players) {
        const k = kit[p.id];
        ctx.beginPath();
        ctx.arc(p.x, p.y, PLAYER_R, 0, 2 * Math.PI);
        ctx.fillStyle = k?.fill ?? "#ccc";
        ctx.fill();
        ctx.lineWidth = 0.3;
        ctx.strokeStyle = k?.edge ?? "#333";
        ctx.stroke();
        ctx.save(); // text must not be drawn upside down
        ctx.translate(p.x, p.y);
        ctx.scale(1, -1);
        ctx.fillStyle = k?.edge ?? "#333";
        ctx.font = "bold 1.3px system-ui";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(String(numbers.get(p.id) ?? ""), 0, 0.08);
        ctx.restore();
      }
      const [bx, by, bz] = f.ball;
      ctx.fillStyle = "rgba(0,0,0,0.35)"; // shadow stays on the ground, the ball rises
      ctx.beginPath();
      ctx.arc(bx + bz * 0.25, by - bz * 0.25, 0.55, 0, 2 * Math.PI);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.strokeStyle = "#111"; // outline keeps the ball visible against white kits
      ctx.lineWidth = 0.18;
      ctx.beginPath();
      ctx.arc(bx, by, 0.6 + bz * 0.08, 0, 2 * Math.PI);
      ctx.fill();
      ctx.stroke();
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [meta, frames]);

  return <canvas ref={canvas} />;
}
