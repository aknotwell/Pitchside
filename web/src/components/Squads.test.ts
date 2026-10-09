import { describe, expect, it } from "vitest";
import type { MatchMeta, Player, Team } from "../types";
import { initialSide, teamOf } from "./Squads";

// 18 players a side like world.py: 11 starters, then the bench (ids h1..h18 / a1..a18).
const team = (id: "h" | "a", name: string): Team => ({
  id, name, short: name.slice(0, 3).toUpperCase(), color: "#888", formation: "4-3-3",
  players: Array.from({ length: 18 }, (_, i): Player => ({ id: `${id}${i + 1}`, name: `${name} ${i + 1}`, number: i + 1, position: i === 0 ? "GK" : "CM", team_id: id })),
});
const meta: MatchMeta = { match_id: "m1", home: team("h", "Home"), away: team("a", "Away"), seed: 1, params_version: "test", competition: "test" };

describe("teamOf (which squad a player belongs to)", () => {
  it("finds home and away players, bench included", () => {
    expect(teamOf(meta, "h4")).toBe("home");
    expect(teamOf(meta, "h15")).toBe("home");  // bench
    expect(teamOf(meta, "a7")).toBe("away");
    expect(teamOf(meta, "a18")).toBe("away");  // bench
  });

  it("is null when no one is followed or the id is in neither squad", () => {
    expect(teamOf(meta, null)).toBeNull();
    expect(teamOf(meta, "x1")).toBeNull();
  });
});

describe("initialSide (the squad the Squads tab opens on)", () => {
  it("opens on home when no one is followed", () => {
    expect(initialSide(meta, null)).toBe("home");
  });

  it("opens on the followed player's team", () => {
    expect(initialSide(meta, "a3")).toBe("away");
    expect(initialSide(meta, "h12")).toBe("home");  // home bench player
  });

  it("falls back to home for an id in neither squad", () => {
    expect(initialSide(meta, "x1")).toBe("home");
  });
});
