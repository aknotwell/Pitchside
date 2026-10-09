import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MatchSummary } from "../types";
import { isLive } from "./MatchPicker";

const NOW = 1_800_000_000;
const match = (over: Partial<MatchSummary>): MatchSummary =>
  ({ id: "m1", displayClock: "12:00", ended: false, home: "Home", away: "Away", homeGoals: 0, awayGoals: 0, updated: NOW, ...over });

describe("isLive (which matches the picker lists under Live)", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW * 1000); });
  afterEach(() => { vi.useRealTimers(); });

  it("lists a running match that is still sending updates", () => {
    expect(isLive(match({ updated: NOW - 10 }))).toBe(true);
  });

  it("leaves out finished matches", () => {
    expect(isLive(match({ ended: true }))).toBe(false);
  });

  it("leaves out a match that stopped updating (crashed or stalled) for 5 minutes", () => {
    expect(isLive(match({ updated: NOW - 299 }))).toBe(true);
    expect(isLive(match({ updated: NOW - 301 }))).toBe(false);
  });
});
