import { useEffect, useRef, useState } from "react";
import type { MatchMeta, Restart, RestartKind } from "../types";

const LABELS: Record<RestartKind, string> = {
  kickoff: "Kick-off", throw_in: "Throw-in", goal_kick: "Goal kick",
  corner: "Corner", free_kick: "Free kick", penalty: "Penalty",
};
// A single sample without a restart (feed jitter, a frame sampled as the kick is taken) must not blink the label.
const HOLD_MS = 500;

/** "Corner · Copperfield" while the ball is dead before a restart, so a quiet pitch doesn't read as buffering. */
export function RestartLabel({ restart, meta }: { restart: Restart | null | undefined; meta: MatchMeta | null | undefined }) {
  const [shown, setShown] = useState<Restart | null>(restart ?? null);
  const kind = restart?.kind, side = restart?.team;
  useEffect(() => {
    if (kind && side) { setShown({ kind, team: side }); return; }
    const id = setTimeout(() => setShown(null), HOLD_MS);
    return () => clearTimeout(id);
  }, [kind, side]);
  const last = useRef(shown);  // keeps the text on screen while it fades out
  if (shown) last.current = shown;
  const r = last.current;
  if (!r || !meta) return null;
  const team = r.team === "h" ? meta.home : meta.away;
  return (
    <div className={`restart-label${shown ? " on" : ""}`} style={{ borderLeftColor: team.color }} aria-hidden={!shown}>
      {LABELS[r.kind]} · {team.name}
    </div>
  );
}
