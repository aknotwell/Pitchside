import { describe, expect, it } from "vitest";
import { arrivals, callUrl, type Call, enqueue, MAX_LATE_S, MAX_QUEUED_S, momentsUp, next, preempts } from "./announcer";
import type { Cue } from "./types";

const cue = (id: string, template: string, display_at_s = 100, extra: Partial<Cue> = {}) =>
  ({ id, matchId: "m1", template, display_at_s, data: {}, moment_id: null, ...extra }) as Cue;
const call = (key: string, rank: number, queuedAt = 0): Call => ({ key, url: `/${key}`, rank, queuedAt });

describe("callUrl", () => {
  it("voices goals, cards and substitutions in the viewer's language", () => {
    expect(callUrl(cue("e9-goal", "goal"), "ja")).toBe("/api/matches/m1/cues/e9-goal/voice?lang=ja");
    expect(callUrl(cue("e4-card", "card"), "en")).toContain("/cues/e4-card/voice");
  });

  it("plays a recap's own audio and stays silent for a recap without any", () => {
    expect(callUrl(cue("r", "panel.recap", 100, { data: { audio: "/api/audio/m1/ht-es.mp3" } }), "es")).toBe("/api/audio/m1/ht-es.mp3");
    expect(callUrl(cue("r", "panel.recap"), "es")).toBeNull();
  });

  it("says nothing for speed tags, shots or explanations", () => {
    for (const t of ["player_tag.speed", "shot", "lower_third.goal_story", "graphic.pressing"]) expect(callUrl(cue("x", t), "en")).toBeNull();
  });
});

describe("arrivals", () => {
  it("calls a cue the frame it goes up, and not again while it stays up", () => {
    const goal = cue("e9-goal", "goal", 100);
    const first = arrivals(new Set(), [goal], "en", 100.1, 1, 5);
    expect(first).toEqual([{ key: "e9-goal", url: "/api/matches/m1/cues/e9-goal/voice?lang=en", rank: 0, queuedAt: 5 }]);
    expect(arrivals(momentsUp([goal]), [goal], "en", 101, 1, 6)).toEqual([]);
  });

  it("skips a late cue that aired too long ago, measured in wall seconds at the playback speed", () => {
    const sub = cue("s", "substitution", 100);
    expect(arrivals(new Set(), [sub], "en", 100 + MAX_LATE_S + 1, 1, 0)).toEqual([]);
    expect(arrivals(new Set(), [sub], "en", 100 + MAX_LATE_S + 1, 4, 0)).toHaveLength(1);
  });

  it("keeps quiet when a viewer switch swaps the recap that is up for the same moment's other variant", () => {
    const casual = cue("ht-casual-en", "panel.recap", 100, { moment_id: "ht", data: { audio: "/a.mp3" } });
    const analyst = { ...casual, id: "ht-analyst-en" };
    expect(arrivals(momentsUp([casual]), [analyst], "en", 105, 1, 0)).toEqual([]);
  });
});

describe("queue", () => {
  it("plays the best rank first, in arrival order within a rank", () => {
    const q = enqueue([call("sub", 2), call("card", 1)], [call("goal", 0), call("card2", 1)]);
    expect(q.map((c) => c.key)).toEqual(["goal", "card", "card2", "sub"]);
  });

  it("drops calls that waited too long", () => {
    const [first, rest] = next([call("old", 0, 0), call("new", 1, 10)], MAX_QUEUED_S + 1);
    expect(first?.key).toBe("new");
    expect(rest).toEqual([]);
  });

  it("lets a goal cut off a card, but not another goal", () => {
    expect(preempts(call("card", 1), [call("goal", 0)])).toBe(true);
    expect(preempts(call("goal", 0), [call("goal2", 0)])).toBe(false);
    expect(preempts(null, [call("goal", 0)])).toBe(false);
  });
});
