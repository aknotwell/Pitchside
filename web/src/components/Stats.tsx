import type { MatchState, Persona } from "../types";

function Bar({ value, left, right, label }: { value: number; left: string; right: string; label: string }) {
  const pct = Math.round(((value + 1) / 2) * 100);
  return (
    <div className="bar" aria-label={`${label}: ${value}`}>
      <div className="bar-label">{label}</div>
      <div className="bar-track"><div className="bar-mid" /><div className="bar-dot" style={{ left: `${pct}%` }} /></div>
      <div className="bar-ends"><span>{left}</span><span>{right}</span></div>
    </div>
  );
}

export function Stats({ state, persona }: { state: MatchState | null; persona: Persona }) {
  if (!state) return <div className="stats empty">No match state yet.</div>;
  const h = state.home, a = state.away;
  const rows: [string, string | number, string | number][] = persona === "analyst"
    ? [
        ["xG", h.xg.toFixed(2), a.xg.toFixed(2)],
        ["Shots (on target)", `${h.shots} (${h.shotsOnTarget})`, `${a.shots} (${a.shotsOnTarget})`],
        ["Possession", `${Math.round(state.possessionHome * 100)}%`, `${Math.round((1 - state.possessionHome) * 100)}%`],
        ["Pass accuracy", `${Math.round(h.passAccuracy * 100)}%`, `${Math.round(a.passAccuracy * 100)}%`],
        ["Final-third entries", h.finalThirdEntries, a.finalThirdEntries],
        ["PPDA", h.ppda ?? "–", a.ppda ?? "–"],
        ["Pressures", h.pressures, a.pressures],
      ]
    : [
        ["Shots", h.shots, a.shots],
        ["Possession", `${Math.round(state.possessionHome * 100)}%`, `${Math.round((1 - state.possessionHome) * 100)}%`],
        ["Chance quality (xG)", h.xg.toFixed(1), a.xg.toFixed(1)],
      ];
  return (
    <div className="stats">
      <table>
        <thead><tr><th>{h.name}</th><th></th><th>{a.name}</th></tr></thead>
        <tbody>{rows.map(([k, x, y]) => (<tr key={k}><td>{x}</td><td className="k">{k}</td><td>{y}</td></tr>))}</tbody>
      </table>
      <Bar value={state.momentum} left={a.name} right={h.name} label="Momentum" />
      <Bar value={state.controlChaos * 2 - 1} left="Control" right="Chaos" label="Game rhythm" />
      {persona === "analyst" && state.topSpeeds.length > 0 && (
        <div className="speeds"><div className="bar-label">Top speeds</div>
          {state.topSpeeds.slice(0, 3).map((s) => <div key={s.playerId}>{s.name} · {s.kmh.toFixed(1)} km/h</div>)}
        </div>
      )}
    </div>
  );
}
