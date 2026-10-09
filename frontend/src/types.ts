// Mirrors the live-stream models in backend/pitchside/schemas.py. Keep them in sync.

export type Side = "home" | "away";

export interface Player {
  id: string;
  name: string;
  number: number;
  position: string;
}

export interface Team {
  id: string;
  name: string;
  short_name: string;
  colors: [string, string];
  formation: string;
  starting_xi: string[];
  bench: string[];
  players: Player[];
}

export interface MatchMeta {
  match_id: string;
  seed: number;
  home: Team;
  away: Team;
  scenario: { name: string } | null;
}

export interface PlayerFrame {
  id: string;
  x: number;
  y: number;
  speed: number;
}

export interface LiveFrame {
  t: number;
  period: number;
  clock: string;
  live: boolean;
  ball: [number, number, number];
  players: PlayerFrame[];
}

export interface MatchEvent {
  id: string;
  t: number;
  clock: string;
  type: string;
  team: Side | null;
  player: string | null;
  outcome: string | null;
}

export type Score = Record<Side, number>;

export type LiveMessage =
  | { type: "meta"; meta: MatchMeta }
  | { type: "frame"; frame: LiveFrame; events: MatchEvent[]; score: Score }
  | { type: "end"; score: Score };
