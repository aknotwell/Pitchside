import type { MatchState } from "../types";

export function Scoreboard({ state, delay, connected, replay = false }: { state: MatchState | null; delay: number; connected: boolean; replay?: boolean }) {
  const meta = state?.meta;
  return (
    <div className="scoreboard">
      <span className="team" style={{ borderColor: meta?.home.color }}>{meta?.home.short ?? state?.home.name ?? "HOME"}</span>
      <span className="score">{state ? `${state.home.goals} – ${state.away.goals}` : "– –"}</span>
      <span className="team" style={{ borderColor: meta?.away.color }}>{meta?.away.short ?? state?.away.name ?? "AWAY"}</span>
      <span className="clock">{state?.displayClock ?? "00:00"}</span>
      <span className={`live ${connected ? "on" : ""}`} title={`Broadcast delay ${delay} s keeps explanations in sync`}>
        {replay ? "REPLAY" : connected ? `LIVE · ${delay}s delay` : "OFFLINE"}
      </span>
    </div>
  );
}
