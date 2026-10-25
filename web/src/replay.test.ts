import { describe, expect, it } from "vitest";
import { type PackedFrame, unpackFrames } from "./replay";

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
