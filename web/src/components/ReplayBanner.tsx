import { useState } from "react";
import type { MatchSummary } from "../types";
import type { ReplaySummary } from "./MatchPicker";
import { startMatch } from "./Producer";

/** Says a replay is playing because nothing is live, and offers the way to something live. */
export function ReplayBanner({ replay, live, onWatchLive, onStarted, onDismiss }: {
  replay: ReplaySummary | undefined; live: MatchSummary | undefined;
  onWatchLive: (id: string) => void; onStarted: () => void; onDismiss: () => void;
}) {
  const [msg, setMsg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const start = async () => {
    setMsg("Starting a live match…");
    setFailed(false);
    try {
      await startMatch(5);
      setMsg("Kicking off: it opens here in about 30 s.");
      onStarted();
    } catch (e) { setMsg(`Could not start: ${(e as Error).message}`); setFailed(true); }
  };
  return (
    <div className="replay-banner" role="status">
      <span className="rb-tag">Replay</span>
      <span className="rb-what">{replay ? `${replay.home} v ${replay.away}` : "Latest match"}</span>
      <span className="rb-msg">{msg ?? (live ? `${live.home} v ${live.away} is live now` : "Nothing live right now")}</span>
      {live
        ? <button className="rb-go" onClick={() => onWatchLive(live.id)}><span className="onair" aria-hidden="true">●</span> Watch live</button>
        : (!msg || failed) && <button className="rb-go" onClick={start}><span className="onair" aria-hidden="true">●</span> {failed ? "Try again" : "Start a live match"}</button>}
      <button className="rb-x" onClick={onDismiss} aria-label="Hide this notice">×</button>
    </div>
  );
}
