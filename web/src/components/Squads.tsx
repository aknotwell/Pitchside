import { useState } from "react";
import type { MatchMeta, Player, Team } from "../types";

export type Side = "home" | "away";

/** Which team a player is on, by squad membership (bench included); null if they're on neither. */
export function teamOf(meta: MatchMeta, playerId: string | null): Side | null {
  if (!playerId) return null;
  if (meta.away.players.some((p) => p.id === playerId)) return "away";
  if (meta.home.players.some((p) => p.id === playerId)) return "home";
  return null;
}

/** The squad the card opens on: the followed player's team, otherwise home. */
export const initialSide = (meta: MatchMeta, selected: string | null): Side => teamOf(meta, selected) ?? "home";

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

function SquadList({ team, selected, onSelect, onPitch }: {
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

type SquadsProps = { selected: string | null; onSelect: (id: string | null) => void; onPitch: Set<string> };

export function Squads({ meta, ...rest }: SquadsProps & { meta: MatchMeta | null | undefined }) {
  if (!meta) return <div className="muted">Squads appear when the match starts.</div>;
  // Keyed by match so the shown team resets for each match, including when meta arrives after the tab opened.
  return <SquadsCard key={meta.match_id} meta={meta} {...rest} />;
}

function SquadsCard({ meta, selected, onSelect, onPitch }: SquadsProps & { meta: MatchMeta }) {
  const [side, setSide] = useState<Side>(() => initialSide(meta, selected));
  return (
    <div className="squads-card">
      <div className="seg team-toggle" role="group" aria-label="Team">
        {(["home", "away"] as const).map((s) => (
          <button key={s} className={side === s ? "on" : ""} aria-pressed={side === s} title={meta[s].name} onClick={() => setSide(s)}>
            <span className="swatch" style={{ background: meta[s].color }} aria-hidden="true" /> {meta[s].name}
          </button>
        ))}
      </div>
      <div className="squads" key={side}>
        <SquadList team={meta[side]} selected={selected} onSelect={onSelect} onPitch={onPitch} />
        <div className="muted">Click a player to follow them on the pitch. Traits (1–99) drive how each player moves and decides.</div>
      </div>
    </div>
  );
}
