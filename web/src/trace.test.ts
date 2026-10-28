import { describe, expect, it } from "vitest";
import { cursorAt, drawn, layoutTrace, REPLAY_MS } from "./trace";
import type { Step } from "./types";

const step = (s: Partial<Step> & Pick<Step, "step">): Step =>
  ({ persona: null, attempt: 0, t_ms: 0, ms: 0, outcome: "ok", detail: null, ...s });

// A goal: the analyst draft quotes a number that isn't in the evidence, is rejected and rewritten
const run: Step[] = [
  step({ step: "director", t_ms: 9000, ms: 40, outcome: "aired" }),
  step({ step: "personalizer", persona: "scout", t_ms: 1500, ms: 3800 }),
  step({ step: "personalizer", persona: "analyst", t_ms: 1500, ms: 3000 }),
  step({ step: "verifier", persona: "analyst", t_ms: 4500, ms: 12, outcome: "rejected", detail: "xG 0.31 not in evidence" }),
  step({ step: "personalizer", persona: "analyst", attempt: 1, t_ms: 4520, ms: 4300 }),
  step({ step: "verifier", persona: "analyst", attempt: 1, t_ms: 8820, ms: 10, outcome: "accepted" }),
  step({ step: "editor", t_ms: 0, ms: 300 }),
  step({ step: "explainer", t_ms: 300, ms: 1200 }),
  step({ step: "personalizer", persona: "casual", t_ms: 1500, ms: 2600 }),
  step({ step: "verifier", persona: "casual", t_ms: 4100, ms: 9, outcome: "accepted" }),
  step({ step: "personalizer", persona: "academy", t_ms: 1500, ms: 2900, outcome: "fallback", detail: "timed out" }),
];

describe("layoutTrace", () => {
  it("lays lanes out in pipeline order with the viewers in the player's order", () => {
    expect(layoutTrace(run)!.lanes.map((l) => l.label)).toEqual(["Editor", "Explainer", "Fan", "Analyst", "Academy", "Scout", "Director"]);
  });

  it("puts every step on one time axis from the run's start to its last step's end", () => {
    const t = layoutTrace(run)!;
    expect(t.total).toBe(9040);
    const explainer = t.lanes[1].bars[0];
    expect([explainer.start, explainer.end]).toEqual([300, 1500]);
    const parallel = t.lanes.filter((l) => l.persona).map((l) => l.bars[0].start);
    expect(parallel).toEqual([1500, 1500, 1500, 1500]);  // the four personalizers run side by side
  });

  it("shows a rejection followed by the retry on the same viewer's lane", () => {
    const analyst = layoutTrace(run)!.lanes.find((l) => l.persona === "analyst")!;
    expect(analyst.bars.map((b) => [b.step, b.attempt, b.tone])).toEqual([
      ["personalizer", 0, "ok"], ["verifier", 0, "rejected"], ["personalizer", 1, "ok"], ["verifier", 1, "ok"],
    ]);
    expect(analyst.bars[1].detail).toBe("xG 0.31 not in evidence");
  });

  it("marks a template takeover apart from a rejection", () => {
    const academy = layoutTrace(run)!.lanes.find((l) => l.persona === "academy")!;
    expect(academy.bars[0].tone).toBe("fallback");
  });

  it("has nothing to lay out for runs recorded before steps were kept", () => {
    expect(layoutTrace(undefined)).toBeNull();
    expect(layoutTrace([])).toBeNull();
  });

  it("keeps a viewer it doesn't know after the known ones", () => {
    const t = layoutTrace([step({ step: "personalizer", persona: "coach", ms: 5 }), step({ step: "personalizer", persona: "scout", ms: 5 }),
                           step({ step: "director", t_ms: 5, ms: 1 })])!;
    expect(t.lanes.map((l) => l.label)).toEqual(["Scout", "coach", "Director"]);
  });
});

describe("replay cursor", () => {
  it("replays any run in the same wall time, keeping relative durations", () => {
    expect(cursorAt(0, 9040)).toBe(0);
    expect(cursorAt(REPLAY_MS / 2, 9040)).toBe(4520);
    expect(cursorAt(REPLAY_MS * 3, 9040)).toBe(9040);
    expect(cursorAt(REPLAY_MS / 2, 120000)).toBe(60000);
  });

  it("draws a bar from its start to its end as the cursor passes", () => {
    const [explainer] = layoutTrace(run)!.lanes[1].bars;
    expect(drawn(explainer, 0)).toBe(0);
    expect(drawn(explainer, 900)).toBe(0.5);
    expect(drawn(explainer, 5000)).toBe(1);
  });

  it("lights a zero-length step as soon as the cursor reaches it", () => {
    const [instant] = layoutTrace([step({ step: "editor", t_ms: 100, ms: 0 }), step({ step: "director", t_ms: 0, ms: 200 })])!.lanes[0].bars;
    expect(drawn(instant, 99)).toBe(0);
    expect(drawn(instant, 100)).toBe(1);
  });
});
