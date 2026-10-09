import type { MatchSummary } from "../types";
import { Choice, Dropdown } from "./Dropdown";

export interface ReplaySummary { matchId: string; home: string; away: string; score: { h: number; a: number } }

/** A match is live while it hasn't ended and is still sending updates (a crashed match goes quiet). */
export const isLive = (m: MatchSummary) => !m.ended && Date.now() / 1000 - m.updated < 300;

const matchLabel = (m: MatchSummary) => `${m.home} ${m.homeGoals}–${m.awayGoals} ${m.away} · ${m.ended ? "FT" : m.displayClock}`;
const replayLabel = (r: ReplaySummary) => `▶ ${r.home} ${r.score?.h}–${r.score?.a} ${r.away}`;

/** Match picker: live matches, then replays in a list capped in height that scrolls. */
export function MatchPicker({ matches, replays, matchId, replayId, onMatch, onReplay }: {
  matches: MatchSummary[]; replays: ReplaySummary[]; matchId: string | null; replayId: string | null;
  onMatch: (id: string) => void; onReplay: (id: string) => void;
}) {
  const live = matches.filter(isLive);
  const currentReplay = replayId ? replays.find((r) => r.matchId === replayId) : undefined;
  const currentMatch = !replayId && matchId ? matches.find((m) => m.id === matchId) : undefined;
  const label = currentReplay ? replayLabel(currentReplay) : currentMatch ? matchLabel(currentMatch)
    : replayId ? "▶ Replay" : live.length ? "Pick a match" : "No live matches";

  return (
    <Dropdown label={label} ariaLabel="Match" className="picker-match">
      {(close) => (
        <>
          <div className="picker-group">Live</div>
          {live.length === 0 && <div className="picker-empty">No live matches right now</div>}
          {live.map((m) => (
            <Choice key={m.id} on={!replayId && m.id === matchId} onPick={() => { onMatch(m.id); close(); }}>
              <span className="picker-live" aria-hidden>●</span> {matchLabel(m)}
            </Choice>
          ))}
          {replays.length > 0 && <div className="picker-group">Replays</div>}
          <div className="picker-replays">
            {replays.map((r) => (
              <Choice key={r.matchId} on={r.matchId === replayId} onPick={() => { onReplay(r.matchId); close(); }}>
                {replayLabel(r)}
              </Choice>
            ))}
          </div>
        </>
      )}
    </Dropdown>
  );
}
