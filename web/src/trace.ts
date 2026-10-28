// A story run's steps laid out as lanes of bars on one time axis, and the replay cursor over them.
import type { Step, StepName, StepOutcome } from "./types";
import { PERSONAS } from "./types";

/** What a bar's colour says: the step did its job, the verifier sent a draft back, a template took over,
 *  the step errored, or it had nothing to do. */
export type Tone = "ok" | "rejected" | "fallback" | "failed" | "skipped";

const TONES: Record<StepOutcome, Tone> = {
  aired: "ok", ok: "ok", accepted: "ok", rejected: "rejected", fallback: "fallback", failed: "failed", dropped: "skipped", skipped: "skipped",
};

export interface Bar extends Step { start: number; end: number; tone: Tone }
export interface Lane { id: string; label: string; persona: string | null; bars: Bar[] }
export interface TraceLayout { total: number; lanes: Lane[] }

const STAGE_LABEL: Record<StepName, string> = {
  editor: "Editor", explainer: "Explainer", personalizer: "Personalizer", verifier: "Verifier", director: "Director",
};

/** Lane order is the pipeline's: editor, explainer, one lane per viewer (its personalizer drafts and the
 *  verifier's checks of them, in viewer order), any step without a viewer, then the director. */
const STAGE_RANK: Partial<Record<StepName, number>> = { editor: 0, explainer: 1, director: 4 };
function rank(step: StepName, persona: string | null) {
  if (!persona) return STAGE_RANK[step] ?? 3;
  const i = PERSONAS.findIndex((p) => p.id === persona);
  return 2 + (i < 0 ? PERSONAS.length : i) / 10;
}

export function layoutTrace(steps: Step[] | undefined): TraceLayout | null {
  if (!steps?.length) return null;
  const lanes = new Map<string, Lane & { rank: number }>();
  for (const s of steps) {
    const id = s.persona ? `persona:${s.persona}` : s.step;
    let lane = lanes.get(id);
    if (!lane) {
      const label = s.persona ? PERSONAS.find((p) => p.id === s.persona)?.label ?? s.persona : STAGE_LABEL[s.step] ?? s.step;
      lane = { id, label, persona: s.persona, bars: [], rank: rank(s.step, s.persona) };
      lanes.set(id, lane);
    }
    lane.bars.push({ ...s, start: s.t_ms, end: s.t_ms + Math.max(0, s.ms), tone: TONES[s.outcome] ?? "ok" });
  }
  const total = Math.max(1, ...steps.map((s) => s.t_ms + Math.max(0, s.ms)));
  return {
    total,
    lanes: [...lanes.values()]
      .sort((a, b) => a.rank - b.rank)
      .map(({ rank: _, ...lane }) => ({ ...lane, bars: lane.bars.sort((a, b) => a.start - b.start) })),
  };
}

/** Wall time the recorded run replays in, whatever its real length, keeping the steps' relative durations. */
export const REPLAY_MS = 2500;

/** Run time the replay cursor has reached `elapsed` wall ms after it started. */
export const cursorAt = (elapsed: number, total: number) => Math.min(total, (Math.max(0, elapsed) / REPLAY_MS) * total);

/** How much of `bar` the cursor has drawn, 0 to 1. A zero-length step lights up as the cursor reaches it. */
export const drawn = (bar: Bar, cursor: number) =>
  cursor < bar.start ? 0 : bar.end <= bar.start ? 1 : Math.min(1, (cursor - bar.start) / (bar.end - bar.start));
