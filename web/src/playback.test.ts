import { describe, expect, it } from "vitest";
import {
  displayClock, goLive, nearestSpeed, pause, play, playheadAt, positionAt, rebase, type ReplayEvent, scoreboardAt, seek, setSpeed,
  settle, speedOf, type Timeline, togglePause, type Transport,
} from "./playback";

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

describe("transport", () => {
  // A live match the simulator plays at 2x: frames from 0 to 600 s, the delayed edge at 580 s.
  const live = (end = 580, rate = 2): Timeline => ({ start: 0, end, live: true, rate });
  const replay: Timeline = { start: 0, end: 1200, live: false, rate: 0 };
  const following: Transport = { anchor: { wall: 0, t0: 0, speed: 1 }, paused: false, followLive: true };
  const at = (t0: number, speed = 1, paused = false): Transport => ({ anchor: { wall: 100, t0, speed }, paused, followLive: false });

  it("rides the delayed live edge while following", () => {
    expect(positionAt(following, 5, live())).toBe(580);
    expect(positionAt(following, 9, live(590))).toBe(590);
    expect(speedOf(following, live())).toBe(2);
  });

  it("pausing live holds the current moment and leaves live", () => {
    const p = pause(following, 100, live());
    expect(p).toMatchObject({ paused: true, followLive: false });
    expect(positionAt(p, 160, live(700))).toBe(580);
    const resumed = play(p, 160, live(700));
    expect(positionAt(resumed, 170, live(720))).toBe(600);  // carries on at the simulator's pace, 2x
  });

  it("seeking leaves live, keeps pause, and clamps to what exists", () => {
    const s = seek(following, 100, live(), 30);
    expect(s.followLive).toBe(false);
    expect(positionAt(s, 110, live())).toBe(50);
    expect(positionAt(seek(at(10, 1, true), 100, live(), 300), 200, live())).toBe(300);
    expect(positionAt(seek(at(10), 100, live(), -50), 100, live())).toBe(0);
    expect(positionAt(seek(at(10), 100, replay, 5000), 100, replay)).toBe(1200);
  });

  it("dragging to the right end of a live match goes live", () => {
    expect(seek(at(10), 100, live(), 579.5)).toMatchObject({ followLive: true, paused: false });
    expect(seek(at(10), 100, live(), 570).followLive).toBe(false);
    expect(seek(at(10), 100, replay, 1200).followLive).toBe(false);
  });

  it("never plays past the delayed live edge", () => {
    expect(positionAt(at(570, 20), 101, live())).toBe(580);
  });

  it("playing faster than the simulator catches up and rejoins live", () => {
    const edge = (wall: number) => live(580 + (wall - 100) * 2);  // the delayed edge moves at 2x
    const fast = setSpeed(seek(following, 100, edge(100), 500), 100, edge(100), 4);
    expect(settle(fast, 130, edge(130))).toBe(fast);
    expect(positionAt(fast, 130, edge(130))).toBe(620);
    expect(settle(fast, 141, edge(141)).followLive).toBe(true);
  });

  it("playing slower than the simulator stays behind", () => {
    const slow = at(580, 1);
    expect(settle(slow, 101, live(582))).toBe(slow);
  });

  it("reaching the end of a finished live match counts as live", () => {
    const tail = at(590, 1);
    expect(settle(tail, 111, live(600, 0)).followLive).toBe(true);
  });

  it("a paused viewer stays put even when the edge passes", () => {
    const p = at(300, 4, true);
    expect(settle(p, 1000, live())).toBe(p);
  });

  it("changing speed keeps the moment and toggling pause round-trips", () => {
    const s = setSpeed(at(100, 1), 110, replay, 10);
    expect(positionAt(s, 110, replay)).toBe(110);
    expect(positionAt(s, 111, replay)).toBe(120);
    const t = togglePause(togglePause(s, 111, replay), 200, replay);
    expect(positionAt(t, 201, replay)).toBe(130);
  });

  it("a replay starts over a little after the final whistle", () => {
    const end = at(1190, 1);
    expect(positionAt(end, 120, replay)).toBe(1200);
    expect(settle(end, 120, replay)).toBe(end);
    expect(positionAt(settle(end, 131, replay), 131, replay)).toBe(0);
  });

  it("maps the simulator's pace to the nearest speed choice", () => {
    expect([0, 1, 1.3, 2.6, 6, 8, 30].map(nearestSpeed)).toEqual([1, 1, 1, 3, 4, 10, 20]);
  });

  it("going live from a replay-like pause resumes playback", () => {
    expect(goLive(at(10, 3, true), 100, live())).toMatchObject({ paused: false, followLive: true });
  });
});
