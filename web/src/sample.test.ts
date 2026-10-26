import { describe, expect, it } from "vitest";
import { lastAtOrBefore, mergeByTime, sampleFrames } from "./sample";
import type { Frame } from "./types";

const frame = (t_s: number, x: number, ball: [number, number, number] = [x, 0, 0], players = [{ id: "h1", x, y: 0 }]): Frame =>
  ({ t_s, ball, players });

describe("sampleFrames", () => {
  const fs = [frame(0, 0), frame(1, 2), frame(2, 4), frame(3, 6)];

  it("returns null with no frames", () => {
    expect(sampleFrames([], 1)).toBeNull();
  });

  it("clamps before the first and after the last frame", () => {
    expect(sampleFrames(fs, -5)).toBe(fs[0]);
    expect(sampleFrames(fs, 99)).toBe(fs[3]);
  });

  it("interpolates the ball linearly between samples", () => {
    const f = sampleFrames([frame(0, 0, [0, 0, 0]), frame(1, 0, [10, -4, 2])], 0.25)!;
    expect(f.t_s).toBe(0.25);
    expect(f.ball).toEqual([2.5, -1, 0.5]);
  });

  it("moves players smoothly along their path (Catmull-Rom through neighbours)", () => {
    expect(sampleFrames(fs, 1.5)!.players[0].x).toBeCloseTo(3);  // constant speed stays on the line
    const xs = [1.1, 1.2, 1.3, 1.4].map((t) => sampleFrames(fs, t)!.players[0].x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));  // never goes backwards
  });

  it("does not smooth through a jump such as a substitution or half-time reset", () => {
    const jump = [frame(0, 0), frame(1, 30)];
    expect(sampleFrames(jump, 0.4)!.players[0].x).toBe(0);
    expect(sampleFrames(jump, 0.6)!.players[0].x).toBe(30);
  });

  it("keeps a player who is missing from the next frame where they were", () => {
    const gone = [frame(0, 5), frame(1, 0, [0, 0, 0], [])];
    expect(sampleFrames(gone, 0.5)!.players).toEqual([{ id: "h1", x: 5, y: 0 }]);
  });

  it("says which restart the ball is dead for until the next sample shows play resumed", () => {
    const corner = { kind: "corner", team: "a" } as const;
    const fs = [{ ...frame(0, 0), restart: corner }, { ...frame(1, 0), restart: corner }, { ...frame(2, 0), restart: null }];
    expect(sampleFrames(fs, 0.5)!.restart).toEqual(corner);
    expect(sampleFrames(fs, 1.5)!.restart).toEqual(corner);
    expect(sampleFrames(fs, 2.5)!.restart).toBeNull();
  });
});

describe("mergeByTime", () => {
  const t = (x: { t: number }) => x.t;
  const items = (...ts: number[]) => ts.map((x) => ({ t: x, src: "held" }));

  it("appends newer items", () => {
    expect(mergeByTime(items(0, 1), items(2, 3), t).map(t)).toEqual([0, 1, 2, 3]);
  });

  it("slots a recording in before what the socket already delivered, without duplicates", () => {
    const merged = mergeByTime(items(5, 6), [0, 1, 5, 2, 6].map((x) => ({ t: x, src: "recording" })), t);
    expect(merged.map(t)).toEqual([0, 1, 2, 5, 6]);
    expect(merged.filter((x) => x.t >= 5).every((x) => x.src === "held")).toBe(true);
  });

  it("leaves the held list as it is when a tail brings nothing it doesn't already hold", () => {
    const held = items(...Array.from({ length: 600 }, (_, i) => i / 10));
    const tail = held.slice(-100).map((x) => ({ ...x, src: "recording" }));
    expect(mergeByTime(held, tail, t)).toBe(held);
    expect(held).toHaveLength(600);
  });

  it("merges like a rebuild of both lists, keeping the held copy of a repeated time", () => {
    let seed = 7;
    const rand = (n: number) => { seed = (seed * 48271) % 2147483647; return seed % n; };
    for (let run = 0; run < 200; run++) {
      const held = [...new Set(Array.from({ length: rand(20) }, () => rand(40)))].sort((a, b) => a - b).map((x) => ({ t: x, src: "held" }));
      const incoming = Array.from({ length: rand(20) }, () => ({ t: rand(50), src: `in${rand(3)}` }));
      if (rand(2)) incoming.sort((a, b) => a.t - b.t);
      const byTime = new Map<number, { t: number; src: string }>();
      for (const x of [...incoming, ...held]) byTime.set(x.t, x);
      const rebuilt = [...byTime.values()].sort((a, b) => a.t - b.t);
      expect(mergeByTime([...held], incoming, t)).toEqual(rebuilt);
    }
  });
});

describe("lastAtOrBefore", () => {
  const xs = [0, 5, 10].map((clockS) => ({ clockS }));
  const k = (x: { clockS: number }) => x.clockS;

  it("finds the latest at or before t, in either direction", () => {
    expect(lastAtOrBefore(xs, 7, k)).toEqual({ clockS: 5 });
    expect(lastAtOrBefore(xs, 10, k)).toEqual({ clockS: 10 });
    expect(lastAtOrBefore(xs, 2, k)).toEqual({ clockS: 0 });
    expect(lastAtOrBefore(xs, -1, k)).toBeNull();
  });
});
