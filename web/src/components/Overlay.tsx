import { API } from "../api";
import type { Cue } from "../types";

function badge(c: Cue) {
  if (c.source === "agent") return <span className="badge ai" title="Written by an agent, every number verified by code">AI · verified</span>;
  if (c.source === "fallback") return <span className="badge fb" title="Deterministic template (model unavailable or draft rejected)">Template</span>;
  return <span className="badge data" title="Computed directly from the event stream">Data</span>;
}

export function Overlay({ cues, onWhy }: { cues: Cue[]; onWhy: (c: Cue) => void }) {
  const panel = cues.find((c) => c.template.startsWith("panel."));
  const thirds = cues.filter((c) => !c.template.startsWith("panel.") && c.template !== "player_tag.speed").slice(0, 2);
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
            <button className="why" onClick={() => onWhy(c)} aria-label={`Why: ${c.title}`}>Why?</button>
          </div>
        ))}
      </div>
    </div>
  );
}
