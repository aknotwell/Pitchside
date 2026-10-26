import { describe, expect, it } from "vitest";
import type { Cue } from "../types";
import { lowerThirds } from "./Overlay";

const cue = (id: string, template = "stat.xg") => ({ id, template }) as Cue;

describe("lowerThirds", () => {
  it("hides a dismissed cue and lets the next one through", () => {
    const cues = [cue("a"), cue("b"), cue("c"), cue("p", "panel.recap"), cue("s", "player_tag.speed")];
    expect(lowerThirds(cues, new Set()).map((c) => c.id)).toEqual(["a", "b"]);
    expect(lowerThirds(cues, new Set(["a"])).map((c) => c.id)).toEqual(["b", "c"]);
  });
});
