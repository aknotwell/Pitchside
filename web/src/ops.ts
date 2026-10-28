// What the ops page shows, from the scorecard endpoint, or from the older ops endpoint where that isn't deployed yet.
import { isLive } from "./components/MatchPicker";
import type { MatchSummary, Scorecard, StoryRun } from "./types";

export interface OpsView {
  /** What the counts cover: the scorecard's window, or only the runs the ops endpoint returned. */
  scope: string;
  runs: Scorecard["runs"];
  /** The ops endpoint doesn't count verifier checks. */
  verifier: Scorecard["verifier"] | null;
  matches: Scorecard["matches"];
  /** The span `matches.finished` covers, as words. */
  finishedWithin: string;
  chaos: Scorecard["chaos"];
  recent: StoryRun[];
}

const span = (h: number) => (h % 24 === 0 && h > 24 ? `${h / 24} days` : `${h} h`);
const day = (t: number) => new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export const fromScorecard = (s: Scorecard): OpsView => ({
  scope: s.since == null ? "no runs yet" : `newest ${s.sampled.toLocaleString()} runs, back to ${day(s.since)}`,
  runs: s.runs, verifier: s.verifier.checked ? s.verifier : null, matches: s.matches,
  finishedWithin: `the last ${span(s.matches_window_h)}`, chaos: s.chaos, recent: s.recent,
});

/** Nearest-rank percentile; null for no values. */
export function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(p * sorted.length) - 1)];
}

export function fromOps(ops: { chaos: Scorecard["chaos"]; runs: StoryRun[] }, matches: MatchSummary[], now: number): OpsView {
  const runs = ops.runs;
  const ran = runs.filter((r) => !r.skipped && r.source !== "duplicate");
  const count = (s: StoryRun["source"]) => ran.filter((r) => r.source === s).length;
  const ms = ran.flatMap((r) => (r.ms != null ? [r.ms] : []));
  return {
    scope: `last ${runs.length} runs`,
    runs: {
      total: ran.length, agent: count("agent"), partial: count("partial"), fallback: count("fallback"),
      skipped: runs.filter((r) => r.skipped).length, resumed: ran.filter((r) => r.resumed_from).length,
      p50_ms: percentile(ms, 0.5), p95_ms: percentile(ms, 0.95),
    },
    verifier: null,
    matches: { live: matches.filter(isLive).length, finished: matches.filter((m) => m.ended && now - m.updated < 86400).length },
    finishedWithin: "the last day",
    chaos: ops.chaos,
    recent: runs.slice(0, 12),
  };
}
