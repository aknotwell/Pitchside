import { describe, expect, it } from "vitest";
import { recordedPace, type Tick } from "./live";
import type { Frame } from "./types";

const frame = (t_s: number): Frame => ({ t_s, ball: [0, 0, 0], players: [] });
/** Ticks `every` match seconds apart, `wallS` server seconds apart (none: an old recording). */
const ticks = (n: number, every: number, wallS?: number): Tick[] =>
  Array.from({ length: n }, (_, i) => ({ type: "tick", frames: [frame(i * every - 0.2), frame(i * every)],
                                         ...(wallS === undefined ? {} : { wallMs: 1_700_000_000_000 + i * wallS * 1000 }) }));

describe("recordedPace", () => {
  it("reads the pace off the ticks' server clocks", () => {
    expect(recordedPace(ticks(30, 3, 1.5))).toEqual({ pace: 2, newest: 87 });
  });

  it("takes ticks without a server clock as a second apart", () => {
    expect(recordedPace(ticks(30, 3))?.pace).toBe(3);
  });

  it("orders ticks that were appended out of order", () => {
    expect(recordedPace(ticks(30, 3, 1.5).reverse())).toEqual({ pace: 2, newest: 87 });
  });

  it("needs a few ticks to go on", () => {
    expect(recordedPace(ticks(2, 3, 1))).toBeNull();
  });
});
