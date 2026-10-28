import { useEffect, useState } from "react";
import { api } from "../api";

/** Starts a simulated match; fast demo speeds play shorter halves. */
export const startMatch = (speed: number) => api.start(speed, speed >= 10 ? 15 : 45);

export function Producer() {
  const [msg, setMsg] = useState<string>("");
  const [ops, setOps] = useState<Awaited<ReturnType<typeof api.ops>> | null>(null);
  const [speed, setSpeed] = useState(5);

  useEffect(() => {
    let alive = true;
    const load = () => api.ops().then((o) => alive && setOps(o)).catch(() => {});
    load();
    const id = setInterval(load, 4000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const start = async () => {
    setMsg("Starting a match…");
    try {
      const r = await startMatch(speed);
      setMsg(`Simulator job started (seed ${r.seed}). It appears in the match list in ~30 s.`);
    } catch (e) { setMsg(`Could not start: ${(e as Error).message}`); }
  };
  const chaos = async () => {
    try { await api.chaos(); setMsg("Chaos armed: the next explanation will kill a storyteller replica mid-workflow."); }
    catch (e) { setMsg(`Chaos failed: ${(e as Error).message}`); }
  };

  return (
    <div className="producer">
      <h3>Producer console <a className="ops-link" href="?page=ops">Ops scorecard →</a></h3>
      <div className="row">
        <label>Speed <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
          {[1, 5, 10, 20].map((s) => <option key={s} value={s}>{s}×</option>)}
        </select></label>
        <button onClick={start}>Start a match</button>
        <button className="danger" onClick={chaos} title="Kills a storyteller replica during a workflow; it resumes from the Cosmos checkpoint">
          Chaos drill
        </button>
      </div>
      {msg && <p className="msg">{msg}</p>}
      <p className="muted">Chaos: {ops?.chaos.armed ? "armed" : "idle"}
        {ops?.chaos.lastFiredAt ? ` · last fired ${new Date(ops.chaos.lastFiredAt * 1000).toLocaleTimeString()}` : ""}</p>
      <table className="runs">
        <thead><tr><th>Moment</th><th>Path</th><th>ms</th><th>Resumed</th></tr></thead>
        <tbody>
          {(ops?.runs ?? []).slice(0, 10).map((r) => (
            <tr key={String(r.moment_id)}>
              <td>{String(r.kind)}</td>
              <td className={`src-${r.source}`}>{String(r.skipped ? "skipped" : r.source)}</td>
              <td>{String(r.ms ?? "")}</td>
              <td>{r.resumed_from ? "✔ checkpoint" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
