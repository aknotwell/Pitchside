import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { fromOps, fromScorecard, type OpsView } from "../ops";
import type { EvidencePack, StoryRun } from "../types";
import { AgentTrace, secs } from "./AgentTrace";

const POLL_MS = 15000;

const PIPELINE: { name: string; service: string; role: string }[] = [
  { name: "Simulator", service: "Container Apps Jobs", role: "one seeded job per match" },
  { name: "Stream", service: "Event Hubs", role: "events + tracking, keyed by match" },
  { name: "Interpreter", service: "Container Apps · KEDA", role: "metrics and moment detectors, scales on hub lag" },
  { name: "Moments", service: "Service Bus sessions", role: "ordered per match, dead-lettered" },
  { name: "Storyteller", service: "Agent Framework · KEDA", role: "Editor → Explainer → Personalizers → Verifier → Director" },
  { name: "Cues", service: "Cosmos DB → Functions Flex", role: "change feed fans cues out" },
  { name: "Screens", service: "Web PubSub", role: "a group per match, viewer and language" },
];
const ALONGSIDE: { service: string; role: string }[] = [
  { service: "Microsoft Foundry", role: "models for the explainer and personalizers" },
  { service: "Cosmos DB checkpoints", role: "a killed storyteller resumes mid-workflow" },
  { service: "Event Grid", role: "match lifecycle → replay packaging job" },
  { service: "Key Vault", role: "the one non-Entra secret" },
];

const EVAL_LABEL: Record<string, { label: string; fmt: (v: number) => string; cmp: string }> = {
  groundedness_mean: { label: "Groundedness, 1–5", fmt: (v) => v.toFixed(2), cmp: "≥" },
  coherence_mean: { label: "Coherence, 1–5", fmt: (v) => v.toFixed(2), cmp: "≥" },
  agent_rate: { label: "Agent-written, passed the verifier", fmt: (v) => `${Math.round(v * 100)}%`, cmp: "≥" },
  p95_ms: { label: "Workflow latency p95", fmt: (v) => secs(v), cmp: "≤" },
};

const time = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const kindLabel = (k: string) => k.replace(/_/g, " ");

function Tile({ label, value, children, wide }: { label: string; value: ReactNode; children?: ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? "tile wide" : "tile"}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {children && <div className="tile-sub">{children}</div>}
    </div>
  );
}

/** Agent, partial and template runs as one part-to-whole bar, with the legend carrying the counts. */
function Share({ agent, partial, fallback }: { agent: number; partial: number; fallback: number }) {
  const total = agent + partial + fallback || 1;
  const parts = [
    { k: "agent", label: "Every viewer AI-written", n: agent },
    { k: "partial", label: "Some on templates", n: partial },
    { k: "fallback", label: "Templates only", n: fallback },
  ];
  return (
    <>
      <div className="share" role="img" aria-label={parts.map((p) => `${p.label} ${p.n}`).join(", ")}>
        {parts.map((p) => p.n > 0 && <span key={p.k} className={`share-${p.k}`} style={{ flexGrow: p.n / total }} title={`${p.label}: ${p.n}`} />)}
      </div>
      <ul className="share-legend">
        {parts.map((p) => <li key={p.k}><i className={`share-${p.k}`} />{p.label} <b>{p.n}</b></li>)}
      </ul>
    </>
  );
}

function RunRow({ run, open, onToggle }: { run: StoryRun; open: boolean; onToggle: () => void }) {
  const src = run.skipped ? "skipped" : run.source ?? "—";
  return (
    <li className={open ? "run open" : "run"}>
      <button className="run-row" aria-expanded={open} onClick={onToggle}>
        <span className="run-t">{time(run.at)}</span>
        <span className="run-kind">{kindLabel(run.kind)}<small>{run.matchId}</small></span>
        <span className={`run-src src-${src}`}>{src === "agent" ? "AI · verified" : src}</span>
        <span className="run-ms">{run.ms != null && !run.skipped ? secs(run.ms) : ""}</span>
        <span className="run-flags">{run.resumed_from && <span className="badge resumed">↻ resumed</span>}
          {run.steps?.some((s) => s.outcome === "rejected") && <span className="badge rej">✕ caught</span>}</span>
        <span className="run-caret" aria-hidden="true">▸</span>
      </button>
      {open && <div className="run-trace"><AgentTrace run={run} /></div>}
    </li>
  );
}

/** One metric's real 95% interval as a band, the real value as a tick and the simulator as a dot. */
function IntervalStrip({ real, interval, synthetic, inside }: { real: string; interval: string; synthetic: string; inside: boolean }) {
  const [lo, hi] = interval.split(/[–-]/).map((v) => parseFloat(v));
  const r = parseFloat(real), s = parseFloat(synthetic);
  if (![lo, hi, r, s].every(Number.isFinite)) return null;
  const min = Math.min(lo, s), max = Math.max(hi, s), pad = (max - min) * 0.12 || 1;
  const x = (v: number) => `${(100 * (v - min + pad)) / (max - min + 2 * pad)}%`;
  return (
    <span className="strip" aria-hidden="true">
      <span className="strip-band" style={{ left: x(lo), right: `calc(100% - ${x(hi)})` }} />
      <span className="strip-real" style={{ left: x(r) }} />
      <span className={inside ? "strip-dot in" : "strip-dot out"} style={{ left: x(s) }} />
    </span>
  );
}

export default function Ops() {
  const [view, setView] = useState<OpsView | null>(null);
  const [down, setDown] = useState(false);
  const [updated, setUpdated] = useState<number | null>(null);
  const [evidence, setEvidence] = useState<EvidencePack | "missing" | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      let next: OpsView | null = null;
      try { next = fromScorecard(await api.scorecard()); }
      catch {
        try {
          const [ops, m] = await Promise.all([api.ops(), api.matches()]);
          next = fromOps(ops, m.matches, Date.now() / 1000);
        } catch { /* both down: keep showing the last good view */ }
      }
      if (!alive) return;
      if (next) { setView(next); setUpdated(Date.now()); setOpenRun((o) => o ?? next.recent.find((r) => r.steps?.length)?.moment_id ?? null); }
      setDown(!next);
    };
    load();
    const id = setInterval(load, POLL_MS);
    api.evidence().then((e) => alive && setEvidence(e)).catch(() => alive && setEvidence("missing"));
    return () => { alive = false; clearInterval(id); };
  }, []);

  const r = view?.runs;
  const ran = r ? r.agent + r.partial + r.fallback : 0;
  const ev = evidence && evidence !== "missing" ? evidence : null;
  const tests = ev?.realism.test.rows ?? [];
  const thresholds = ev?.eval.thresholds;

  return (
    <div className="app ops-page">
      <header>
        <h1>MatchLens <small>ops scorecard</small></h1>
        <a className="hdr-link" href="./">← Back to the player</a>
        <span className={down ? "ops-pulse down" : "ops-pulse"}>
          <span aria-hidden="true">●</span> {down ? "API unreachable, showing the last reading" : updated ? `Live · updated ${new Date(updated).toLocaleTimeString()} · every 15 s` : "Connecting…"}
        </span>
      </header>
      <main className="ops">
        <p className="ops-intro">
          Is this real cloud-native engineering? The top row is read from the running system{view ? ` (${view.scope})` : ""}.
          Below it is the evidence committed with the code: calibration against real match data, the load test and the eval gate.
        </p>

        <section className="tiles" aria-label="Live system">
          <Tile label="Story runs" value={r ? r.total.toLocaleString() : "—"}>{r ? `${r.skipped.toLocaleString()} more skipped by the overlay budget` : ""}</Tile>
          <Tile label="Written by agents" wide value={r && ran ? `${Math.round((100 * r.agent) / ran)}%` : "—"}>
            {r && <Share agent={r.agent} partial={r.partial} fallback={r.fallback} />}
          </Tile>
          <Tile label="Workflow latency, p50" value={r?.p50_ms != null ? secs(r.p50_ms) : "—"}>{r?.p95_ms != null ? `p95 ${secs(r.p95_ms)}, moment to cues written` : ""}</Tile>
          <Tile label="Invented numbers caught" value={view?.verifier ? view.verifier.rejected.toLocaleString() : "—"}>
            {view?.verifier
              ? `of ${view.verifier.checked.toLocaleString()} drafts checked · ${view.verifier.retried_ok} fixed on retry`
              : "Counted by the scorecard endpoint"}
          </Tile>
          <Tile label="Checkpoint resumes" value={r ? r.resumed : "—"}>runs that lost their replica and resumed from Cosmos DB</Tile>
          <Tile label="Live matches" value={view ? view.matches.live : "—"}>{view ? `${view.matches.finished_24h} finished in the last day` : ""}</Tile>
          <Tile label="Chaos drill" value={view ? <span className={view.chaos.armed ? "warn" : ""}>{view.chaos.armed ? "Armed" : "Idle"}</span> : "—"}>
            {view?.chaos.lastFiredAt ? `last fired ${time(view.chaos.lastFiredAt)}` : "kills a storyteller mid-workflow"}
          </Tile>
        </section>

        <section className="card arch" aria-label="Architecture">
          <h2>The pipeline, event to screen</h2>
          <ol className="flow">
            {PIPELINE.map((p) => (
              <li key={p.name}><span className="flow-name">{p.name}</span><b>{p.service}</b><span>{p.role}</span></li>
            ))}
          </ol>
          <ul className="alongside">
            {ALONGSIDE.map((a) => <li key={a.service}><b>{a.service}</b> {a.role}</li>)}
          </ul>
        </section>

        <div className="ops-cols">
          <section className="card">
            <h2>Recent story runs</h2>
            {view && view.recent.length === 0 && <p className="muted">No runs yet. Start a match from the player's Producer tab.</p>}
            <ul className="runs-list">
              {view?.recent.map((run) => (
                <RunRow key={run.moment_id} run={run} open={openRun === run.moment_id}
                        onToggle={() => setOpenRun((o) => (o === run.moment_id ? null : run.moment_id))} />
              ))}
            </ul>
          </section>

          <section className="card">
            <h2>Eval gate</h2>
            {ev ? (
              <>
                <p className="muted">{ev.eval.summary.moments} golden moments, {ev.eval.summary.variants_scored} variants judged by a Foundry model · {ev.eval.at.slice(0, 10)}</p>
                <table className="otable">
                  <thead><tr><th>Check</th><th className="n">Value</th><th className="n">Gate</th><th className="n">Pass</th></tr></thead>
                  <tbody>
                    {Object.entries(ev.eval.checks).map(([k, ok]) => {
                      const m = EVAL_LABEL[k], v = ev.eval.summary[k], t = thresholds?.[k];
                      return (
                        <tr key={k}>
                          <td>{m?.label ?? k}</td>
                          <td className="n">{v == null ? "—" : m ? m.fmt(v) : String(v)}</td>
                          <td className="n muted">{t == null ? "" : `${m?.cmp ?? ""} ${m ? m.fmt(t) : t}`}</td>
                          <td className="n">{ok ? <span className="pass">✓ pass</span> : <span className="fail">✕ fail</span>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            ) : <p className="muted">{evidence === "missing" ? "No evidence file in this build." : "Loading…"}</p>}

            <h2 className="mt">Load test</h2>
            {ev ? (
              <>
                <p className="muted">{ev.loadtest.matches} simultaneous matches at {ev.loadtest.speed}× · {ev.loadtest.at.slice(0, 10)}. Replicas KEDA added under load:</p>
                <ul className="replicas">
                  {Object.entries(ev.loadtest.peak_replicas).map(([svc, n]) => {
                    const peak = Math.max(...Object.values(ev.loadtest.peak_replicas), 1);
                    return (
                      <li key={svc}><span>{svc}</span><span className="rep-track"><span style={{ width: `${(100 * n) / peak}%` }} /></span><b>{n}</b></li>
                    );
                  })}
                </ul>
                <table className="otable">
                  <thead><tr><th>Cue path</th><th className="n">Cues</th><th className="n">p50</th><th className="n">p95</th><th className="n">Max</th></tr></thead>
                  <tbody>
                    {Object.entries(ev.loadtest.latency).filter(([, l]) => l.n > 0).map(([path, l]) => (
                      <tr key={path}><td>{path}</td><td className="n">{l.n}</td>
                        {[l.p50_ms, l.p95_ms, l.max_ms].map((v, i) => <td key={i} className="n">{v == null ? "—" : secs(v)}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
                <p className="muted">Latency is from the triggering event entering Event Hubs to the cue arriving over the WebSocket.</p>
              </>
            ) : <p className="muted">{evidence === "missing" ? "" : "Loading…"}</p>}
          </section>
        </div>

        {ev && (
          <section className="card">
            <h2>Is the synthetic football realistic?</h2>
            <p className="muted">The simulator is fictional but calibrated against SkillCorner open data (A-League 2024/25, MIT).</p>
            <div className="ops-cols">
              <div>
                <h3>Held-out simulated matches ({ev.realism.heldout.matches})</h3>
                <table className="otable">
                  <thead><tr><th>Metric</th><th className="n">Real</th><th className="n">Simulated</th><th className="n">Error</th></tr></thead>
                  <tbody>{ev.realism.heldout.rows.map((row) => (
                    <tr key={row.metric}><td>{row.metric}</td><td className="n">{row.real}</td><td className="n">{row.synthetic}</td><td className="n">{row.error}</td></tr>
                  ))}</tbody>
                </table>
                <p className="muted">These seeds were also used while hand-tuning, so this table is optimistic. The test set is the honest check.</p>
              </div>
              <div>
                <h3>Test set: data never used to fit or tune · {tests.filter((t) => t.inside).length} of {tests.length} inside</h3>
                <table className="otable test">
                  <thead><tr><th>Metric</th><th className="n">Real</th><th className="n">Sim</th><th className="strip-h">Real <i className="k-tick" /> 95% <i className="k-band" /> Sim <i className="k-dot" /></th><th className="n">Inside?</th></tr></thead>
                  <tbody>{tests.map((row) => (
                    <tr key={row.metric} title={`${row.metric}: real ${row.real} (95% ${row.interval}), simulated ${row.synthetic}`}>
                      <td>{row.metric}</td><td className="n">{row.real}</td><td className="n">{row.synthetic}</td>
                      <td className="strip-cell"><IntervalStrip {...row} /></td>
                      <td className="n">{row.inside ? <span className="pass">✓ yes</span> : <span className="fail">✕ no</span>}</td>
                    </tr>
                  ))}</tbody>
                </table>
                <p className="muted">Only 4 real test matches, so each interval is itself rough: "no" means outside the spread of those matches, not a significance test.</p>
              </div>
            </div>
          </section>
        )}
        {ev && <p className="sources muted">Sources: {Object.values(ev.sources).join(" · ")}</p>}
      </main>
    </div>
  );
}
