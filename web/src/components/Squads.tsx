import type { MatchMeta, Player, Team } from "../types";

const LABELS: Record<string, string> = {
  pace: "Pace", acceleration: "Acceleration", stamina: "Stamina", passing: "Passing", vision: "Vision",
  crossing: "Crossing", dribbling: "Dribbling", first_touch: "First touch", finishing: "Finishing",
  long_shots: "Long shots", heading: "Heading", tackling: "Tackling", marking: "Marking",
  positioning: "Positioning", aggression: "Aggression", composure: "Composure", work_rate: "Work rate",
  flair: "Flair", reflexes: "Reflexes",
};

function best(p: Player, n = 3): [string, number][] {
  const skip = p.position === "GK" ? new Set(["finishing", "long_shots", "dribbling", "crossing"]) : new Set(["reflexes"]);
  return Object.entries(p.traits ?? {}).filter(([k]) => !skip.has(k)).sort((a, b) => b[1] - a[1]).slice(0, n);
}

function Side({ team, selected, onSelect, onPitch }: {
  team: Team; selected: string | null; onSelect: (id: string | null) => void; onPitch: Set<string>;
}) {
  const style = team.style ?? {};
  return (
    <div className="squad">
      <div className="squad-head" style={{ borderLeftColor: team.color }}>
        <b>{team.name}</b> <span className="muted">{team.formation}{team.style_name ? ` · ${team.style_name}` : ""}</span>
        {team.style && (
          <div className="muted">
            Press {Math.round((style.press ?? 0) * 100)} · Line {Math.round((style.line ?? 0) * 100)} ·
            Tempo {Math.round((style.tempo ?? 0) * 100)} · Direct {Math.round((style.directness ?? 0) * 100)}
          </div>
        )}
      </div>
      <ul>
        {team.players.map((p) => (
          <li key={p.id} className={`${selected === p.id ? "on" : ""} ${onPitch.has(p.id) ? "" : "bench"}`}
              onClick={() => onSelect(selected === p.id ? null : p.id)}>
            <span className="no">{p.number}</span>
            <span className="who"><b>{p.name}</b> <span className="muted">{p.position}{p.role ? ` · ${p.role}` : ""}{p.foot === "L" ? " · left-footed" : ""}</span>
              {p.traits && <span className="traits">{best(p).map(([k, v]) => `${LABELS[k] ?? k} ${v}`).join(" · ")}</span>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Squads({ meta, selected, onSelect, onPitch }: {
  meta: MatchMeta | null | undefined; selected: string | null; onSelect: (id: string | null) => void; onPitch: Set<string>;
}) {
  if (!meta) return <div className="muted">Squads appear when the match starts.</div>;
  return (
    <div className="squads">
      <Side team={meta.home} selected={selected} onSelect={onSelect} onPitch={onPitch} />
      <Side team={meta.away} selected={selected} onSelect={onSelect} onPitch={onPitch} />
      <div className="muted">Click a player to follow them on the pitch. Traits (1–99) drive how each player moves and decides.</div>
    </div>
  );
}
