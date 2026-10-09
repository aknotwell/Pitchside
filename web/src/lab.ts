import type { Frame, MatchMeta, Player, Team } from "./types";

/** Scripted movers for tuning the procedural athletes (?lab=1): each one exercises a gait. */
const ROUTES: { id: string; at: (t: number) => [number, number] }[] = [
  { id: "h7", at: (t) => [-20 + ((t * 8.5) % 40), 6] },                          // flat-out sprint
  { id: "h9", at: (t) => [Math.cos(t * 0.35) * 9, Math.sin(t * 0.35) * 9] },     // jog round a curve
  { id: "a4", at: (t) => [8 + (t % 6 < 3 ? (t % 3) * 4 : 12 - (t % 3) * 4), -8 + Math.sin(t * 2.2) * 3] }, // zigzag cuts
  { id: "a6", at: (t) => [-6 - Math.sin(t * 0.5) * 6, -4] },                     // backpedal and step up (faces the ball)
  { id: "h2", at: () => [-12, -10] },                                            // idle
  { id: "h4", at: () => [-14, 6] },                                              // shields the ball from a8
  { id: "a8", at: (t) => [-14 - 1.2 * Math.cos(t * 0.6), 6 + 1.2 * Math.sin(t * 0.6)] },
  { id: "h10", at: () => [4, 14] },                                              // strikes the ball every 4 s
];

function ballAt(t: number): [number, number, number] {
  const k = t % 8;
  if (k < 4) return [-14 + 0.45, 6, 0];          // at the shielding player's feet
  const f = k - 4;
  if (f < 0.6) return [4.5, 14, 0];              // set for the kick
  const d = Math.min(30, (f - 0.6) * 16);
  return [4.5 + d, 14, Math.max(0, d * 0.35 - d * d * 0.012)];
}

function team(id: "h" | "a", color: string, name: string): Team {
  const players: Player[] = Array.from({ length: 11 }, (_, i) => ({ id: `${id}${i + 1}`, name: `${name} ${i + 1}`, number: i + 1, position: i === 0 ? "GK" : "CM", team_id: id }));
  return { id, name, short: name.slice(0, 3).toUpperCase(), color, formation: "4-3-3", players };
}

export const LAB_META: MatchMeta = {
  match_id: "lab", home: team("h", "#1f8f4e", "Lab Home"), away: team("a", "#6d3fd1", "Lab Away"),
  seed: 0, params_version: "lab", competition: "Athlete lab",
};

const t0 = performance.now() / 1000;
export function labSample(): Frame {
  const t = performance.now() / 1000 - t0;
  const players = ROUTES.map((r) => { const [x, y] = r.at(t); return { id: r.id, x, y }; });
  return { t_s: t, ball: ballAt(t), players };
}
