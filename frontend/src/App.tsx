import { Pitch, teamKits, type Kit } from "./Pitch";
import { useLiveMatch } from "./useLiveMatch";

function Swatch({ kit }: { kit: Kit }) {
  return <span className="swatch" style={{ background: kit.fill, borderColor: kit.edge }} />;
}

/** Match options come from the page URL, e.g. /?home=kingsmere&away=ravenholm&speed=4 */
export default function App() {
  const { meta, score, clock, status, frames } = useLiveMatch(location.search);
  const kits = meta && teamKits(meta);

  return (
    <main className="app">
      {meta && kits && (
        <header className="scoreboard">
          <Swatch kit={kits.home} />
          {meta.home.name}
          <span className="score">
            {score.home} – {score.away}
          </span>
          {meta.away.name}
          <Swatch kit={kits.away} />
          <span className="clock">{clock}</span>
        </header>
      )}
      {meta && <Pitch meta={meta} frames={frames} />}
      <p className="status">{status}</p>
    </main>
  );
}
