import { describe, expect, it } from "vitest";
import type { Cue } from "../types";
import { audiences, momentOf } from "./WhyDrawer";

const variant = (persona: string, lang: string) => ({ id: `m1-${persona}-${lang}`, persona, lang, title: `${persona} ${lang}` }) as Cue;

describe("audiences", () => {
  it("gives each viewer, in the player's order, its copy in the language being watched", () => {
    const cues = [variant("scout", "es"), variant("analyst", "en"), variant("analyst", "es"), variant("casual", "es"), variant("academy", "es")];
    expect(audiences(cues, "es").map((a) => [a.label, a.cue?.id ?? null])).toEqual([
      ["Fan", "m1-casual-es"], ["Analyst", "m1-analyst-es"], ["Academy", "m1-academy-es"], ["Scout", "m1-scout-es"],
    ]);
  });

  it("leaves a viewer empty when no copy aired for it in that language", () => {
    const cards = audiences([variant("casual", "en"), variant("analyst", "ja")], "en");
    expect(cards.map((a) => a.cue?.id ?? null)).toEqual(["m1-casual-en", null, null, null]);
  });
});

describe("momentOf", () => {
  const cue = (c: Partial<Cue>) => ({ id: "e7-shot", template: "shot", moment_id: null, ...c }) as Cue;
  it("finds an agent cue's moment, and the GOAL card's, and no moment behind other data cards", () => {
    expect(momentOf(cue({ id: "e9-goal-casual-en", template: "lower_third.goal_story", moment_id: "e9-goal" }))).toBe("e9-goal");
    expect(momentOf(cue({ id: "e9-goal", template: "goal" }))).toBe("e9-goal");
    expect(momentOf(cue({}))).toBeNull();
  });
});
