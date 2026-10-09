import { useEffect, useRef, useState } from "react";
import type { MatchSummary } from "../types";

export interface ReplaySummary { matchId: string; home: string; away: string; score: { h: number; a: number } }

/** A match is live while it hasn't ended and is still sending updates (a crashed match goes quiet). */
export const isLive = (m: MatchSummary) => !m.ended && Date.now() / 1000 - m.updated < 300;

const matchLabel = (m: MatchSummary) => `${m.home} ${m.homeGoals}–${m.awayGoals} ${m.away} · ${m.ended ? "FT" : m.displayClock}`;
const replayLabel = (r: ReplaySummary) => `▶ ${r.home} ${r.score?.h}–${r.score?.a} ${r.away}`;

/**
 * Match picker: live matches, then replays in a list capped in height that scrolls.
 * A native <select> can't cap its popup, so a long replay list covered half the screen.
 */
export function MatchPicker({ matches, replays, matchId, replayId, onMatch, onReplay }: {
  matches: MatchSummary[]; replays: ReplaySummary[]; matchId: string | null; replayId: string | null;
  onMatch: (id: string) => void; onReplay: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const live = matches.filter(isLive);

  useEffect(() => {  // close on a click outside or Escape
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const currentReplay = replayId ? replays.find((r) => r.matchId === replayId) : undefined;
  const currentMatch = !replayId && matchId ? matches.find((m) => m.id === matchId) : undefined;
  const label = currentReplay ? replayLabel(currentReplay) : currentMatch ? matchLabel(currentMatch)
    : replayId ? "▶ Replay" : live.length ? "Pick a match" : "No live matches";
  const pick = (fn: () => void) => () => { fn(); setOpen(false); };

  return (
    <div className="picker" ref={root}>
      <button className="picker-btn" aria-label="Match" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {label} <span aria-hidden>▾</span>
      </button>
      {open && (
        <div className="picker-menu" role="listbox" aria-label="Matches">
          <div className="picker-group">Live</div>
          {live.length === 0 && <div className="picker-empty">No live matches right now</div>}
          {live.map((m) => (
            <button key={m.id} role="option" aria-selected={!replayId && m.id === matchId}
                    className={!replayId && m.id === matchId ? "on" : ""} onClick={pick(() => onMatch(m.id))}>
              <span className="picker-live" aria-hidden>●</span> {matchLabel(m)}
            </button>
          ))}
          {replays.length > 0 && <div className="picker-group">Replays</div>}
          <div className="picker-replays">
            {replays.map((r) => (
              <button key={r.matchId} role="option" aria-selected={r.matchId === replayId}
                      className={r.matchId === replayId ? "on" : ""} onClick={pick(() => onReplay(r.matchId))}>
                {replayLabel(r)}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
