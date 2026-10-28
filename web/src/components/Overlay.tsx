import { useState } from "react";
import { API } from "../api";
import type { Cue } from "../types";

export function badge(c: Cue) {
  if (c.source === "agent") return <span className="badge ai" title="Written by an agent, every number verified by code">AI · verified</span>;
  if (c.source === "fallback") return <span className="badge fb" title="Deterministic template (model unavailable or draft rejected)">Template</span>;
  return <span className="badge data" title="Computed directly from the event stream">Data</span>;
}

/** Lower-thirds the viewer can close. Dismissal is per cue id, so the next cue still appears, and a
 *  dismissed one stays under Moments with its Why? intact. */
export function lowerThirds(cues: Cue[], dismissed: ReadonlySet<string>): Cue[] {
  return cues.filter((c) => isLowerThird(c) && !dismissed.has(c.id)).slice(0, 2);
}

/** Recap panels and speed tags air elsewhere on screen; everything else is a lower-third. */
export const isLowerThird = (c: Cue) => !c.template.startsWith("panel.") && c.template !== "player_tag.speed";

export function Overlay({ cues, onWhy }: { cues: Cue[]; onWhy: (c: Cue) => void }) {
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  const panel = cues.find((c) => c.template.startsWith("panel."));
  const thirds = lowerThirds(cues, dismissed);
  return (
    <div className="overlay" aria-live="polite">
      {panel && (
        <div className="panel">
          <div className="panel-title">{panel.title} {badge(panel)}</div>
          <p>{panel.text}</p>
          {typeof panel.data.audio === "string" && <audio controls preload="none" src={`${API}${panel.data.audio}`} aria-label="Listen to the recap" />}
          <button className="why" onClick={() => onWhy(panel)}>Why?</button>
        </div>
      )}
      <div className="thirds">
        {thirds.map((c) => (
          <div key={c.id} className={`third t-${c.template.split(".")[0]} ${c.template.includes("goal") ? "goal" : ""}`}>
            <div className="third-title">{c.title} {badge(c)}</div>
            <div className="third-text">{c.text}</div>
            <div className="third-actions">
              <button className="why" onClick={() => onWhy(c)} aria-label={`Why: ${c.title}`}>Why?</button>
              <button className="dismiss" onClick={() => setDismissed((d) => new Set(d).add(c.id))} aria-label={`Dismiss: ${c.title}`} title="Dismiss">×</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
