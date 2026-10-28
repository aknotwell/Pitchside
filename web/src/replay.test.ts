import { describe, expect, it } from "vitest";
import type { Cue } from "./types";
import { OPENING_LEAD_S, openingAt, type PackedFrame, unpackFrames } from "./replay";

describe("unpackFrames", () => {
  it("reads the restart the ball is dead for", () => {
    const packed: PackedFrame[] = [[0, 0, 0, [[-1, 2], null], 0, ["kickoff", "h"]], [0.2, 1, 0, [[-1, 2], [3, 4]], 0.3, null]];
    const [dead, live] = unpackFrames(["h1", "a1"], packed);
    expect(dead).toEqual({ t_s: 0, ball: [0, 0, 0], players: [{ id: "h1", x: -1, y: 2 }], restart: { kind: "kickoff", team: "h" } });
    expect(live.restart).toBeNull();
    expect(live.players).toHaveLength(2);
  });

  it("plays replays packaged before ball height and restarts were recorded", () => {
    const [f] = unpackFrames(["h1"], [[5, 1, 2, [[0, 0]]]]);
    expect(f).toEqual({ t_s: 5, ball: [1, 2, 0], players: [{ id: "h1", x: 0, y: 0 }], restart: null });
  });
});

const at = (display_at_s: number, template: string, priority = 3) => ({ id: `${template}@${display_at_s}`, display_at_s, template, priority }) as Cue;

describe("openingAt", () => {
  it("starts a little before the first goal, so its explanation airs within seconds", () => {
    const cues = [at(30, "card", 2), at(4127.7, "goal", 1), at(249.9, "goal", 1), at(252.9, "lower_third.goal_story", 1)];
    expect(openingAt(cues)).toBeCloseTo(249.9 - OPENING_LEAD_S);
  });

  it("falls back to the most important lower-third when nobody scored", () => {
    const cues = [at(100, "pass.quality", 3), at(900, "card", 2), at(1200, "card", 2), at(50, "player_tag.speed", 0), at(5400, "panel.recap", 0)];
    expect(openingAt(cues)).toBe(900 - OPENING_LEAD_S);
  });

  it("never starts before kick-off, and starts at kick-off with nothing to show", () => {
    expect(openingAt([at(5, "goal", 1)])).toBe(0);
    expect(openingAt([at(50, "player_tag.speed", 0), at(5400, "panel.recap", 0)])).toBeNull();
  });
});
