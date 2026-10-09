import { describe, expect, it } from "vitest";
import { displayClock, playheadAt, rebase, type ReplayEvent, scoreboardAt } from "./playback";

const ev = (clock_s: number, type: string, extra: Partial<ReplayEvent> = {}): ReplayEvent =>
  ({ clock_s, type, team_id: null, outcome: null, period: 1, ...extra });

describe("displayClock (same rule as sim/engine.py display_clock)", () => {
  it("counts regulation time as mm:ss", () => {
    expect(displayClock(0, 1, 0, 45)).toBe("00:00");
    expect(displayClock(235.9, 1, 0, 45)).toBe("03:55");
    expect(displayClock(236, 1, 0, 45)).toBe("03:56");
    expect(displayClock(2700, 1, 0, 45)).toBe("45:00");
  });

  it("shows first-half stoppage time as 45+n", () => {
    expect(displayClock(2700.5, 1, 0, 45)).toBe("45+1:00");
    expect(displayClock(2700 + 167, 1, 0, 45)).toBe("45+3:47");
  });

  it("starts the second half at 45:00 from its own kick-off", () => {
    expect(displayClock(2807.6, 2, 2807.6, 45)).toBe("45:00");
    expect(displayClock(2807.6 + 61, 2, 2807.6, 45)).toBe("46:01");
    expect(displayClock(2807.6 + 2700 + 30, 2, 2807.6, 45)).toBe("90+1:30");
  });

  it("scales with short demo halves", () => {
    expect(displayClock(600, 1, 0, 10)).toBe("10:00");
    expect(displayClock(610, 2, 610, 10)).toBe("10:00");
    expect(displayClock(1215, 2, 610, 10)).toBe("20+1:05");
  });

  it("never shows a negative time before kick-off", () => {
    expect(displayClock(5, 2, 10, 45)).toBe("45:00");
  });
});

describe("scoreboardAt", () => {
  const events: ReplayEvent[] = [
    ev(0, "period_start"),
    ev(100, "shot", { team_id: "h", outcome: "goal" }),
    ev(200, "shot", { team_id: "a", outcome: "saved" }),
    ev(300, "shot", { team_id: "a", outcome: "goal" }),
    ev(2800, "period_end"),
    ev(2800, "carry", { period: 2 }),  // resolves on the whistle but is labelled with the next period
    ev(2801, "period_start", { period: 2 }),
    ev(3000, "shot", { team_id: "h", outcome: "goal", period: 2 }),
  ];

  it("counts only goals up to the playhead", () => {
    expect(scoreboardAt(events, 50, 45)).toMatchObject({ home: 0, away: 0 });
    expect(scoreboardAt(events, 250, 45)).toMatchObject({ home: 1, away: 0 });
    expect(scoreboardAt(events, 400, 45)).toMatchObject({ home: 1, away: 1 });
    expect(scoreboardAt(events, 3100, 45)).toMatchObject({ home: 2, away: 1 });
  });

  it("keeps running between events instead of jumping", () => {
    const clocks = [150, 151, 152, 153].map((t) => scoreboardAt(events, t, 45).clock);
    expect(clocks).toEqual(["02:30", "02:31", "02:32", "02:33"]);
  });

  it("stops at the half-time whistle and takes the period from the whistles", () => {
    expect(scoreboardAt(events, 2800, 45)).toMatchObject({ period: 1, clock: "45+2:40" });
    expect(scoreboardAt(events, 2800.5, 45)).toMatchObject({ period: 1, clock: "45+2:40" });
    expect(scoreboardAt(events, 2801, 45)).toMatchObject({ period: 2, clock: "45:00" });
    expect(scoreboardAt(events, 2801 + 90, 45)).toMatchObject({ period: 2, clock: "46:30" });
  });
});

describe("playhead and speed changes", () => {
  it("advances at the anchor's speed", () => {
    expect(playheadAt({ wall: 10, t0: 100, speed: 4 }, 12.5)).toBe(110);
  });

  it("changing speed carries on from the current position", () => {
    const a = { wall: 0, t0: 0, speed: 4 };
    const b = rebase(a, 10, 1);
    expect(playheadAt(b, 10)).toBe(playheadAt(a, 10));  // no jump at the moment of the change
    expect(playheadAt(b, 12)).toBe(42);  // then 1× from 40 s
  });
});
