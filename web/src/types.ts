export type Persona = "analyst" | "casual" | "academy" | "scout";
export const PERSONAS: { id: Persona; label: string; hint: string }[] = [
  { id: "casual", label: "Fan", hint: "Plain words, why it matters" },
  { id: "analyst", label: "Analyst", hint: "Tactics and metrics" },
  { id: "academy", label: "Academy", hint: "A coach's lesson for young players" },
  { id: "scout", label: "Scout", hint: "What the moment says about the players" },
];
export type Lang = "en" | "es" | "ja";

export interface PlayerPos { id: string; x: number; y: number }
export type RestartKind = "kickoff" | "throw_in" | "goal_kick" | "corner" | "free_kick" | "penalty";
/** The restart the ball is dead for, and the side taking it. Absent in old replays and while play is live. */
export interface Restart { kind: RestartKind; team: "h" | "a" }
export interface Frame { t_s: number; ball: [number, number, number]; players: PlayerPos[]; restart?: Restart | null }

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
  // Pass quality and shot speed (absent from state docs written before they were added)
  avgPassLengthM?: number; avgPassDifficulty?: number; hardPasses?: number; hardPassAccuracy?: number | null;
  fastestShotKmh?: number;
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
  probeMs?: number;
  ms?: number;
  problems?: string[] | null;
}

export interface AskAnswer { answer: string; tools: string[]; model: string; reflex?: Reflex }

/** How a story run ended: every viewer written by the agents, some of them, none (templates), or a repeat. */
export type RunSource = "agent" | "partial" | "fallback" | "duplicate";
export type StepName = "editor" | "explainer" | "personalizer" | "verifier" | "director";
export type StepOutcome = "aired" | "skipped" | "ok" | "dropped" | "failed" | "accepted" | "rejected" | "fallback";

/** One executor's turn in a story run. `t_ms` is from the run's start; personalizers for different
 *  viewers overlap, and a verifier rejection is followed by `attempt` 1 for the same persona. */
export interface Step {
  step: StepName; persona: string | null; attempt: number; t_ms: number; ms: number;
  outcome: StepOutcome; detail: string | null;
}

/** The agent workflow's record of one moment. Runs recorded before steps were kept have none. */
export interface StoryRun {
  moment_id: string; matchId: string; kind: string; source: RunSource | null; ms: number | null; at: number;
  started_at?: number; resumed_from: string | null; skipped: string | null;
  personas?: Record<string, string>; steps?: Step[];
}

/** Everything behind one moment: the run and every viewer and language variant it aired. */
export interface MomentDetail { moment: Record<string, unknown> | null; run: StoryRun | null; cues: Cue[] }

export interface Scorecard {
  generated_at: number;
  /** The newest `sampled` runs are summed; `since` is when the oldest of them ran (null with none). */
  since: number | null; sampled: number; matches_window_h: number;
  runs: { total: number; agent: number; partial: number; fallback: number; skipped: number; resumed: number; p50_ms: number | null; p95_ms: number | null };
  verifier: { checked: number; rejected: number; retried_ok: number };
  matches: { live: number; finished: number };
  chaos: { armed: boolean; lastFiredAt?: number };
  recent: StoryRun[];
}

export interface Latency { n: number; p50_ms: number | null; p95_ms: number | null; max_ms: number | null; mean_ms: number | null }

/** The static evidence pack the judges' page reads: calibration, load test and eval gate results. */
export interface EvidencePack {
  realism: {
    heldout: { matches: number; rows: { metric: string; real: string; synthetic: string; error: string }[] };
    test: { rows: { metric: string; real: string; interval: string; synthetic: string; inside: boolean }[] };
  };
  loadtest: { at: string; matches: number; speed: number; peak_replicas: Record<string, number>; latency: Record<string, Latency> };
  /** `thresholds` names each check's gate; files written before it was added lack it. */
  eval: { at: string; summary: Record<string, number>; checks: Record<string, boolean>; thresholds?: Record<string, number> };
  sources: Record<string, string>;
}
