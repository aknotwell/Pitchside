import { describe, expect, it } from "vitest";
import { sampleFrames } from "./sample";
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
