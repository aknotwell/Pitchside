import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { connect, keepLoadingRecording, liveFeed, recordedPace, type Tick } from "./live";
import type { Cue, Frame } from "./types";

vi.mock("./api", () => ({ API: "", api: { cues: vi.fn(), negotiate: vi.fn(), match: vi.fn() } }));

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

  it("ignores ticks recorded again after an interpreter restart", () => {
    const first = ticks(30, 3, 1.5);
    const replayed = first.slice(-3).map((t) => ({ ...t, wallMs: t.wallMs! + 60_000 }));
    expect(recordedPace([...first, ...replayed])).toEqual({ pace: 2, newest: 87 });
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
    void keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(asked).toEqual([0, 2, 7, 17, 37]);
    expect(f.frames.map((x) => x.t_s)).toEqual([-0.2, 0, 0.8, 1, 1.8, 2]);
  });

  it("keeps retrying an empty recording, created but not yet appended to", async () => {
    vi.useFakeTimers();
    const asked: number[] = [];
    const t0 = Date.now();
    vi.stubGlobal("fetch", async () => {
      asked.push((Date.now() - t0) / 1000);
      return new Response(asked.length < 3 ? "" : ticks(3, 1, 1).map((x) => JSON.stringify(x)).join("\n"));
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const f = liveFeed();
    f.connected = true;
    void keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(asked).toEqual([0, 2, 7]);
    expect(f.frames[0].t_s).toBe(-0.2);
  });

  it("waits for the socket before asking", async () => {
    vi.useFakeTimers();
    const asked = recordingAt(0);
    const f = liveFeed();
    void keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(asked).toEqual([]);
    f.connected = true;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(asked).toHaveLength(1);
  });

  /** The recording endpoint over `recorded`: the whole of it, or with `from` only the bytes after that
   *  offset (204 when there are none, 400 past the end). Returns the URLs asked for. */
  function serve(recorded: () => Tick[]) {
    const asked: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      asked.push(url);
      const all = new TextEncoder().encode(recorded().map((x) => JSON.stringify(x) + "\n").join(""));
      const from = Number(new URL(url, "http://api").searchParams.get("from") ?? 0);
      if (from > all.length) return new Response("", { status: 400 });
      return from === all.length ? new Response(null, { status: 204 }) : new Response(all.slice(from));
    });
    return asked;
  }
  const bytes = (ts: Tick[]) => new TextEncoder().encode(ts.map((x) => JSON.stringify(x) + "\n").join("")).length;

  it("counts its offset in bytes of what it was sent, so the next tail starts on the tick after", async () => {
    vi.useFakeTimers();
    const named = (n: number): Tick[] => ticks(n, 1, 1).map((t) => ({ ...t, frames: t.frames!.map((x) => ({ ...x, players: [{ id: "atlético-9", x: 0, y: 0 }] })) }));
    let recorded = named(3);
    const asked = serve(() => recorded);
    const f = liveFeed();
    f.connected = true;
    void keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(f.recording.size).toBe(bytes(recorded));

    recorded = named(5);
    f.recording.wanted++;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(asked[1]).toBe(`/api/matches/m1/recording?from=${bytes(named(3))}`);
    expect(f.recording.size).toBe(bytes(recorded));
    expect(f.frames.map((x) => x.t_s)).toEqual(named(5).flatMap((t) => t.frames!.map((x) => x.t_s)));
  });

  it("reads the whole recording again when its offset is past the end", async () => {
    vi.useFakeTimers();
    let recorded = ticks(5, 1, 1);
    const asked = serve(() => recorded);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const f = liveFeed();
    f.connected = true;
    void keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(1_000);

    recorded = ticks(2, 1, 1);  // shorter than the offset this viewer holds
    f.recording.wanted++;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(asked.slice(1)).toEqual([`/api/matches/m1/recording?from=${bytes(ticks(5, 1, 1))}`, "/api/matches/m1/recording"]);
    expect(f.recording.size).toBe(bytes(recorded));
    expect(f.recording.loaded).toBe(f.recording.wanted);
  });

  it("after a drop asks only for what was recorded since, and stops once the match has ended", async () => {
    vi.useFakeTimers();
    let recorded = ticks(3, 1, 1);
    const asked = serve(() => recorded);
    const f = liveFeed();
    f.connected = true;
    const done = keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(asked).toEqual(["/api/matches/m1/recording"]);

    const before = bytes(recorded);
    recorded = ticks(8, 1, 1);  // aired while the socket was down
    f.recording.wanted++;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(asked[1]).toBe(`/api/matches/m1/recording?from=${before}`);
    expect(f.frames.map((x) => x.t_s)).toEqual(ticks(8, 1, 1).flatMap((t) => t.frames!.map((x) => x.t_s)));

    recorded = [...recorded, { type: "tick", frames: [frame(8)], state: { clockS: 8, ended: true } as Tick["state"] }];
    f.recording.wanted++;
    await vi.advanceTimersByTimeAsync(1_000);
    await done;
    expect(asked).toHaveLength(3);
    expect(f.frames.at(-1)?.t_s).toBe(8);
  });

  it("looks once more a few seconds after a backfill, for ticks whose appends were still landing", async () => {
    vi.useFakeTimers();
    let recorded = ticks(3, 1, 1);
    const asked = serve(() => recorded);
    const f = liveFeed();
    f.connected = true;
    void keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(1_000);

    recorded = ticks(5, 1, 1);
    f.recording.wanted++;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(asked).toHaveLength(2);
    recorded = ticks(6, 1, 1);  // sent before the reconnect, appended after the backfill read
    await vi.advanceTimersByTimeAsync(10_000);
    expect(asked).toHaveLength(3);
    expect(f.frames.at(-1)?.t_s).toBe(5);
    await vi.advanceTimersByTimeAsync(40_000);
    expect(asked).toHaveLength(3);
  });

  it("keeps its offset fresh while connected, so a reconnect late in the match fetches a short tail", async () => {
    vi.useFakeTimers();
    const t0 = Date.now();
    const asked = serve(() => ticks(Math.floor((Date.now() - t0) / 1000) + 3, 1, 1));  // a tick a second
    const sizes = () => asked.map((u) => Number(new URL(u, "http://api").searchParams.get("from") ?? 0));
    const f = liveFeed();
    f.connected = true;
    void keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(30 * 60_000);
    expect(asked.length).toBeGreaterThanOrEqual(25);
    expect(asked.length).toBeLessThanOrEqual(35);
    const latest = f.recording.size;

    f.recording.wanted++;  // the socket came back after a drop
    await vi.advanceTimersByTimeAsync(1_000);
    expect(sizes().at(-1)).toBe(latest);
    expect(f.recording.size - latest).toBeLessThan(latest / 20);
  });

  it("takes nothing new since the last load as loaded, not as a failure to retry", async () => {
    vi.useFakeTimers();
    const asked = serve(() => ticks(3, 1, 1));
    const f = liveFeed();
    f.connected = true;
    void keepLoadingRecording(f, "m1", new AbortController().signal);
    await vi.advanceTimersByTimeAsync(1_000);
    f.recording.wanted++;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(asked).toHaveLength(3);  // the load, the backfill, and its one look again
    expect(f.recording.loaded).toBe(f.recording.wanted);
  });

  it("stops once the player is gone", async () => {
    vi.useFakeTimers();
    const asked = recordingAt(Infinity);
    const f = liveFeed();
    f.connected = true;
    const gone = new AbortController();
    void keepLoadingRecording(f, "m1", gone.signal);
    await vi.advanceTimersByTimeAsync(3_000);
    gone.abort();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(asked).toEqual([0, 2]);
  });

  it("cancels a download in progress once the player is gone", async () => {
    vi.useFakeTimers();
    let cancelled = false;
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener("abort", () => { cancelled = true; reject(init.signal!.reason); });
    }));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const f = liveFeed();
    f.connected = true;
    const gone = new AbortController();
    void keepLoadingRecording(f, "m1", gone.signal);
    gone.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(cancelled).toBe(true);
    expect(warn).not.toHaveBeenCalled();
  });
});

describe("connect", () => {
  class FakeSocket {
    static all: FakeSocket[] = [];
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onmessage: ((ev: { data: string }) => void) | null = null;
    constructor(public url: string) { FakeSocket.all.push(this); }
    close() {}
    send(msg: object) { this.onmessage?.({ data: JSON.stringify(msg) }); }
  }
  const cue = (id: string) => ({ id, display_at_s: 1 }) as Cue;
  const settle = () => new Promise((r) => setTimeout(r, 0));
  afterEach(() => { vi.unstubAllGlobals(); FakeSocket.all = []; });

  it("a viewer switched away from never writes their late cue history", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    let arrive: (v: { cues: Cue[] }) => void = () => {};
    vi.mocked(api.cues).mockReturnValueOnce(new Promise((r) => { arrive = r; })).mockResolvedValueOnce({ cues: [cue("analyst-1")] });
    vi.mocked(api.negotiate).mockResolvedValue({ url: "ws://x" });
    const f = liveFeed();
    const stopCasual = connect(f, "m1", "casual", "en");
    await settle();
    FakeSocket.all[0].onopen?.();
    stopCasual();
    connect(f, "m1", "analyst", "en");
    await settle();
    FakeSocket.all[1].onopen?.();
    await settle();
    arrive({ cues: [cue("casual-1")] });
    await settle();
    expect([...f.byId.keys()]).toEqual(["analyst-1"]);
  });

  it("joining fetches the cue history once, and none of it is late", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.mocked(api.cues).mockReset().mockResolvedValue({ cues: [cue("a"), cue("b")] });
    vi.mocked(api.negotiate).mockResolvedValue({ url: "ws://x" });
    const f = liveFeed();
    connect(f, "m1", "casual", "en");
    await settle();
    FakeSocket.all[0].onopen?.();
    await settle();
    expect(api.cues).toHaveBeenCalledTimes(1);
    expect([...f.byId.keys()]).toEqual(["a", "b"]);
    expect(f.late).toEqual([]);
  });

  it("after a drop it reconnects, merges the cues it missed as late ones, and asks for the recording again", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.mocked(api.cues).mockReset().mockResolvedValueOnce({ cues: [cue("before")] })
      .mockResolvedValue({ cues: [cue("before"), cue("missed")] });
    vi.mocked(api.negotiate).mockResolvedValue({ url: "ws://x" });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const f = liveFeed();
    connect(f, "m1", "casual", "en");
    await vi.advanceTimersByTimeAsync(0);
    FakeSocket.all[0].onopen?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(f.connected).toBe(true);
    expect(f.recording.wanted).toBe(1);

    FakeSocket.all[0].onclose?.();
    expect(f.connected).toBe(false);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(FakeSocket.all).toHaveLength(2);
    FakeSocket.all[1].onopen?.();
    await vi.advanceTimersByTimeAsync(0);
    expect(f.connected).toBe(true);
    expect([...f.byId.keys()]).toEqual(["before", "missed"]);
    expect(f.late.map((c) => c.id)).toEqual(["missed"]);
    expect(api.cues).toHaveBeenCalledTimes(2);
    expect(f.recording.wanted).toBe(2);
    vi.useRealTimers();
  });

  it("a viewer or language switch asks for what aired between the two sockets, and joining doesn't", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.mocked(api.cues).mockResolvedValue({ cues: [] });
    vi.mocked(api.negotiate).mockResolvedValue({ url: "ws://x" });
    const f = liveFeed();
    const stopCasual = connect(f, "m1", "casual", "en");
    await settle();
    FakeSocket.all[0].onopen?.();
    expect(f.recording.wanted).toBe(1);
    FakeSocket.all[0].send({ type: "tick", frames: [{ t_s: 5, ball: [0, 0, 0], players: [] }] });

    stopCasual();
    connect(f, "m1", "analyst", "en");
    await settle();
    FakeSocket.all[1].onopen?.();
    expect(f.recording.wanted).toBe(2);
  });

  it("a closed socket's last messages and close don't touch the next one's feed", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.mocked(api.cues).mockResolvedValue({ cues: [] });
    vi.mocked(api.negotiate).mockResolvedValue({ url: "ws://x" });
    const f = liveFeed();
    const stopFirst = connect(f, "m1", "casual", "en");
    await settle();
    const [first] = FakeSocket.all;
    first.onopen?.();
    stopFirst();
    connect(f, "m1", "casual", "es");
    await settle();
    FakeSocket.all[1].onopen?.();
    first.send({ type: "cue", cue: cue("english") });
    first.onclose?.();
    FakeSocket.all[1].send({ type: "cue", cue: cue("spanish") });
    expect(f.connected).toBe(true);
    expect([...f.byId.keys()]).toEqual(["spanish"]);
    expect(f.late.map((c) => c.id)).toEqual(["spanish"]);
  });
});
