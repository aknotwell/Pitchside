import type { Cue, Frame, MatchMeta } from "../types";

const L = 105, W = 68;

export function Pitch({ frame, meta, cues, follow }: { frame: Frame | null; meta?: MatchMeta | null; cues: Cue[]; follow?: string | null }) {
  const numbers = new Map<string, number>();
  meta?.home.players.forEach((p) => numbers.set(p.id, p.number));
  meta?.away.players.forEach((p) => numbers.set(p.id, p.number));
  const colors = { h: meta?.home.color ?? "#1f6feb", a: meta?.away.color ?? "#d1242f" };
  const tags = new Map<string, Cue>();
  for (const c of cues) if (c.template === "player_tag.speed" && typeof c.data.playerId === "string") tags.set(c.data.playerId, c);

  return (
    <svg className="pitch" viewBox={`${-L / 2 - 3} ${-W / 2 - 3} ${L + 6} ${W + 6}`} role="img"
         aria-label="Live 2D pitch with player positions">
      <rect x={-L / 2 - 3} y={-W / 2 - 3} width={L + 6} height={W + 6} className="grass" />
      {Array.from({ length: 10 }, (_, i) => (
        <rect key={i} x={-L / 2 + i * (L / 10)} y={-W / 2} width={L / 10} height={W} className={i % 2 ? "stripe" : "stripe alt"} />
      ))}
      <g className="lines">
        <rect x={-L / 2} y={-W / 2} width={L} height={W} />
        <line x1={0} y1={-W / 2} x2={0} y2={W / 2} />
        <circle cx={0} cy={0} r={9.15} />
        <circle cx={0} cy={0} r={0.4} className="spot" />
        {[-1, 1].map((s) => (
          <g key={s}>
            <rect x={s > 0 ? L / 2 - 16.5 : -L / 2} y={-20.16} width={16.5} height={40.32} />
            <rect x={s > 0 ? L / 2 - 5.5 : -L / 2} y={-9.16} width={5.5} height={18.32} />
            <rect x={s > 0 ? L / 2 : -L / 2 - 2} y={-3.66} width={2} height={7.32} className="goal" />
            <circle cx={s * (L / 2 - 11)} cy={0} r={0.4} className="spot" />
          </g>
        ))}
      </g>
      {frame?.players.map((p) => {
        const team = p.id[0] as "h" | "a";
        const tag = tags.get(p.id);
        return (
          <g key={p.id} transform={`translate(${p.x.toFixed(2)} ${(-p.y).toFixed(2)})`}>
            {tag && <circle r={3.2} className="tag-ring" />}
            {follow === p.id && <circle r={2.6} className="follow-ring" />}
            <circle r={1.5} fill={colors[team]} className="player" />
            <text y={0.55} className="num">{numbers.get(p.id) ?? ""}</text>
            {tag && (
              <g transform="translate(2.5 -3.5)">
                <rect width={24} height={5.5} rx={1} className="tag-box" />
                <text x={1.2} y={2.3} className="tag-name">{String(tag.data.player ?? "")}</text>
                <text x={1.2} y={4.6} className="tag-val">{`${tag.data.kmh} km/h`}</text>
              </g>
            )}
          </g>
        );
      })}
      {frame && (
        <g transform={`translate(${frame.ball[0].toFixed(2)} ${(-frame.ball[1]).toFixed(2)})`}>
          <circle r={0.9 + Math.min(1.5, frame.ball[2] * 0.15)} className="ball" />
        </g>
      )}
      {!frame && <text x={0} y={0} className="waiting">Waiting for the live feed…</text>}
    </svg>
  );
}
