import { afterEach, describe, expect, it, vi } from "vitest";
import { keepLoadingRecording, liveFeed, recordedPace, type Tick } from "./live";
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

describe("keepLoadingRecording", () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  /** A recording endpoint that 404s until `availableAt` seconds in; returns the times it was asked. */
  function recordingAt(availableAt: number) {
    const asked: number[] = [];
    const t0 = Date.now();
    vi.stubGlobal("fetch", async () => {
      const t = (Date.now() - t0) / 1000;
      asked.push(t);
      const body = ticks(3, 1, 1).map((x) => JSON.stringify(x)).join("\n");
      return t >= availableAt ? new Response(body) : new Response("", { status: 404 });
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    return asked;
  }

  it("retries a 404 at kick-off with back-off until the recording exists", async () => {
    vi.useFakeTimers();
    const asked = recordingAt(30);
    const f = liveFeed();
    f.connected = true;
    const done = keepLoadingRecording(f, "m1", () => true);
    await vi.advanceTimersByTimeAsync(60_000);
    await done;
    expect(asked).toEqual([0, 2, 7, 17, 37]);
    expect(f.frames.map((x) => x.t_s)).toEqual([-0.2, 0, 0.8, 1, 1.8, 2]);
  });

  it("waits for the socket before asking", async () => {
    vi.useFakeTimers();
    const asked = recordingAt(0);
    const f = liveFeed();
    void keepLoadingRecording(f, "m1", () => true);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(asked).toEqual([]);
    f.connected = true;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(asked).toHaveLength(1);
  });

  it("stops once the player is gone", async () => {
    vi.useFakeTimers();
    const asked = recordingAt(Infinity);
    const f = liveFeed();
    f.connected = true;
    let active = true;
    void keepLoadingRecording(f, "m1", () => active);
    await vi.advanceTimersByTimeAsync(3_000);
    active = false;
    await vi.advanceTimersByTimeAsync(120_000);
    expect(asked).toEqual([0, 2]);
  });
});
