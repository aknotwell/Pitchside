import { describe, expect, it } from "vitest";
import { keepConnected, type SocketLike } from "./socket";
import type { Cue } from "./types";

class FakeSocket {
  onopen: SocketLike["onopen"] = null;
  onclose: SocketLike["onclose"] = null;
  onmessage: SocketLike["onmessage"] = null;
  closed = false;
  constructor(public url: string) {}
  close() { this.closed = true; this.fire("close"); }
  fire(kind: "open" | "close") {
    const handler = kind === "open" ? this.onopen : this.onclose;
    handler?.call(this as unknown as WebSocket, new Event(kind) as Event & CloseEvent);
  }
}

const cue = (id: string) => ({ id }) as Cue;
const flush = () => new Promise((r) => setTimeout(r, 0));

function harness() {
  const sockets: FakeSocket[] = [];
  const timers: { ms: number; fn: () => void; cancelled: boolean }[] = [];
  const merged: string[][] = [];
  const status: boolean[] = [];
  let n = 0;
  let history: Cue[] = [];
  const stop = keepConnected({
    negotiate: async () => ({ url: `wss://feed/${++n}` }),
    cues: async () => ({ cues: history }),
    open: (url) => { const s = new FakeSocket(url); sockets.push(s); return s as unknown as SocketLike; },
    wait: (ms, fn) => { const t = { ms, fn, cancelled: false }; timers.push(t); return () => { t.cancelled = true; }; },
  }, {
    onMessage: () => {},
    onCues: (cs) => merged.push(cs.map((c) => c.id)),
    onConnected: (c) => status.push(c),
  });
  return { sockets, timers, merged, status, stop, setHistory: (h: Cue[]) => { history = h; } };
}

describe("keepConnected", () => {
  it("negotiates again and reconnects when the socket closes, with growing backoff", async () => {
    const h = harness();
    await flush();
    h.sockets[0].fire("open");
    h.sockets[0].fire("close");  // Wi-Fi blip
    expect(h.status).toEqual([true, false]);
    expect(h.timers.map((t) => t.ms)).toEqual([1000]);

    h.timers[0].fn();
    await flush();
    expect(h.sockets.map((s) => s.url)).toEqual(["wss://feed/1", "wss://feed/2"]);  // a fresh token
    h.sockets[1].fire("close");  // fails before opening: back off further
    h.timers[1].fn();
    await flush();
    h.sockets[2].fire("close");
    expect(h.timers.map((t) => t.ms)).toEqual([1000, 2000, 5000]);
  });

  it("merges cues sent while the socket was opening or down", async () => {
    const h = harness();
    await flush();
    h.setHistory([cue("a"), cue("gap")]);
    h.sockets[0].fire("open");
    await flush();
    expect(h.merged).toEqual([["a", "gap"]]);

    h.sockets[0].fire("close");
    h.setHistory([cue("a"), cue("gap"), cue("while-down")]);
    h.timers[0].fn();
    await flush();
    h.sockets[1].fire("open");
    await flush();
    expect(h.merged[1]).toContain("while-down");
    expect(h.status).toEqual([true, false, true]);
  });

  it("stops for good: no reconnect after the viewer leaves", async () => {
    const h = harness();
    await flush();
    h.sockets[0].fire("open");
    h.stop();
    expect(h.sockets[0].closed).toBe(true);
    expect(h.timers).toEqual([]);
  });
});
