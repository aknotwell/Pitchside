import type { MatchState } from "../types";

export function Scoreboard({ state, delay, connected, replay = false, paused = false, behind = false }: {
  state: MatchState | null; delay: number; connected: boolean; replay?: boolean; paused?: boolean; behind?: boolean;
}) {
  const meta = state?.meta;
  return (
    <div className="scoreboard">
      <span className="team" style={{ borderColor: meta?.home.color }}>{meta?.home.short ?? state?.home.name ?? "HOME"}</span>
      <span className="score">{state ? `${state.home.goals} – ${state.away.goals}` : "– –"}</span>
      <span className="team" style={{ borderColor: meta?.away.color }}>{meta?.away.short ?? state?.away.name ?? "AWAY"}</span>
      <span className="clock">{state?.displayClock ?? "00:00"}</span>
      <span className={`live ${connected && !behind ? "on" : ""}`} title={`Broadcast delay ${delay} s keeps explanations in sync`}>
        {replay ? (paused ? "REPLAY · PAUSED" : "REPLAY") : !connected ? "OFFLINE" : paused ? "LIVE · PAUSED" : behind ? "LIVE · BEHIND" : `LIVE · ${delay}s delay`}
      </span>
    </div>
  );
}
