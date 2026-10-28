import { describe, expect, it } from "vitest";
import { fromOps, fromScorecard, percentile } from "./ops";
import type { MatchSummary, Scorecard, StoryRun } from "./types";

const run = (r: Partial<StoryRun>): StoryRun =>
  ({ moment_id: "m", matchId: "x", kind: "goal", source: "agent", ms: 1000, at: 0, resumed_from: null, skipped: null, ...r });
const match = (m: Partial<MatchSummary>): MatchSummary =>
  ({ id: "x", displayClock: "10:00", ended: false, home: "A", away: "B", homeGoals: 0, awayGoals: 0, updated: 0, ...m });

describe("percentile", () => {
  it("takes the nearest rank", () => {
    expect(percentile([5, 1, 4, 2, 3], 0.5)).toBe(3);
    expect(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95)).toBe(10);
    expect(percentile([], 0.5)).toBeNull();
  });
});

describe("fromOps", () => {
  const now = Date.now() / 1000;  // isLive reads the real clock
  const view = fromOps({
    chaos: { armed: false },
    runs: [
      run({ ms: 8000 }), run({ ms: 12000, resumed_from: "ckpt" }), run({ source: "partial", ms: 15000 }), run({ source: "fallback", ms: 3000 }),
      run({ source: null, ms: 2, skipped: "overlay budget" }), run({ source: "duplicate", ms: 1 }),
    ],
  }, [match({ updated: now - 30 }), match({ ended: true, updated: now - 3600 }), match({ ended: true, updated: now - 90000 })], now);

  it("counts the runs that ran by how they ended, apart from skips and duplicates", () => {
    expect(view.runs).toMatchObject({ total: 4, agent: 2, partial: 1, fallback: 1, skipped: 1, resumed: 1 });
  });

  it("takes latency only from runs that ran", () => {
    expect([view.runs.p50_ms, view.runs.p95_ms]).toEqual([8000, 15000]);
  });

  it("counts live matches and the ones that finished in the last day", () => {
    expect(view.matches).toEqual({ live: 1, finished: 1 });
  });

  it("says it has no verifier counts rather than claiming none were caught", () => {
    expect(view.verifier).toBeNull();
    expect(view.scope).toBe("last 6 runs");
  });
});

describe("fromScorecard", () => {
  const card = (checked: number): Scorecard => ({
    generated_at: 0, since: Date.UTC(2026, 9, 20, 12) / 1000, sampled: 3, matches_window_h: 168,
    runs: { total: 3, agent: 3, partial: 0, fallback: 0, skipped: 0, resumed: 0, p50_ms: 1, p95_ms: 2 },
    verifier: { checked, rejected: 0, retried_ok: 0 }, matches: { live: 0, finished: 2 }, chaos: { armed: false }, recent: [],
  });

  it("says how many runs it summed and how far back they go", () => {
    expect(fromScorecard(card(5)).scope).toMatch(/^newest 3 runs, back to Oct 20$/);
    expect(fromScorecard({ ...card(0), since: null, sampled: 0 }).scope).toBe("no runs yet");
    expect(fromScorecard(card(5)).finishedWithin).toBe("the last 7 days");
  });

  it("shows no verifier counts until runs recorded their steps", () => {
    expect(fromScorecard(card(0)).verifier).toBeNull();
    expect(fromScorecard(card(4)).verifier?.checked).toBe(4);
  });
});
