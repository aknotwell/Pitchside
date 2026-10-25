export type Persona = "analyst" | "casual";
export type Lang = "en" | "es" | "ja";

export interface PlayerPos { id: string; x: number; y: number }
export interface Frame { t_s: number; ball: [number, number, number]; players: PlayerPos[] }

export interface Evidence {
  key: string; label: string; value: number | string; unit?: string;
  before?: number | null; after?: number | null;
}

export interface Cue {
  id: string; matchId: string; display_at_s: number; ttl_s: number; priority: number;
  template: string; persona: string; lang: string; title: string; text: string;
  data: Record<string, unknown>;
  evidence: { event_ids: string[]; metrics: Evidence[] };
  verified: boolean; source: "template" | "agent" | "fallback";
  moment_id?: string | null; trace_id?: string | null;
}

export interface TeamSummary {
  id: string; name: string; goals: number; shots: number; shotsOnTarget: number; xg: number;
  passes: number; passAccuracy: number; finalThirdEntries: number; ppda: number | null;
  pressures: number; turnovers: number;
}

export interface MatchState {
  id: string; clockS: number; displayClock: string; period: number; ended: boolean;
  home: TeamSummary; away: TeamSummary; possessionHome: number; momentum: number; controlChaos: number;
  topSpeeds: { playerId: string; name: string; kmh: number }[];
  meta?: MatchMeta | null;
}

export interface Player {
  id: string; name: string; number: number; position: string; team_id: string;
  role?: string; foot?: "R" | "L"; traits?: Record<string, number>;
}
export interface Team {
  id: string; name: string; short: string; color: string; formation: string; players: Player[];
  style_name?: string; style?: Record<string, number>;
}
export interface MatchMeta { match_id: string; home: Team; away: Team; seed: number; params_version: string; competition: string; period_min?: number }

export interface MatchSummary {
  id: string; displayClock: string; ended: boolean; home: string; away: string;
  homeGoals: number; awayGoals: number; updated: number;
}

export interface Reflex {
  outcome: "answer" | "decline" | "pass";
  capability: string | null;
  tier: 0 | 1 | null;
  confidence: number;
  reason: string;
  ms: number;
}

export interface AskAnswer { answer: string; tools: string[]; model: string; reflex?: Reflex }
