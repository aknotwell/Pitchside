import { useEffect, useMemo, useRef, useState } from "react";
import { cursorAt, drawn, layoutTrace, type Bar } from "../trace";
import type { StoryRun } from "../types";
import { PERSONAS } from "../types";

const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
const pct = (ms: number, total: number) => `${(100 * ms) / total}%`;
export const secs = (ms: number) => `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;

/** The explainer and personalizers call the model; the editor, verifier and director are code. */
const MODEL_STEPS = new Set(["explainer", "personalizer"]);
const STEP_VERB: Record<string, string> = {
  editor: "Editor", explainer: "Explainer", personalizer: "Personalizer", verifier: "Verifier", director: "Director",
};
const personaLabel = (id: string) => PERSONAS.find((p) => p.id === id)?.label ?? id;

const RUN_SOURCE: Record<string, { label: string; cls: string }> = {
  agent: { label: "Every viewer AI-written", cls: "ai" },
  partial: { label: "Some viewers on templates", cls: "fb" },
  fallback: { label: "Templates only", cls: "fb" },
  duplicate: { label: "Duplicate delivery, ignored", cls: "data" },
};

function describe(b: Bar) {
  const who = b.persona ? ` · ${personaLabel(b.persona)}` : "";
  const attempt = b.attempt ? ` · retry ${b.attempt}` : "";
  return `${STEP_VERB[b.step] ?? b.step}${who}${attempt}: ${b.outcome}, ${b.ms} ms${b.detail ? `. ${b.detail}` : ""}`;
}

/** A recorded story run: its steps as a timeline that replays in ~2.5 s when shown, or, for runs
 *  recorded before steps were kept, what it ended with. */
export function AgentTrace({ run }: { run: StoryRun | null }) {
  const layout = useMemo(() => layoutTrace(run?.steps), [run]);
  const [cursor, setCursor] = useState(0);
  const [take, setTake] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);

  useEffect(() => {  // the replay starts once the trace is on screen, not while it's scrolled out of view
    const el = root.current;
    if (!el || seen) return;
    if (typeof IntersectionObserver !== "function") { setSeen(true); return; }
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) setSeen(true); }, { threshold: 0.6 });
    io.observe(el);
    return () => io.disconnect();
  }, [layout, seen]);

  useEffect(() => {
    if (!layout) return;
    if (reducedMotion()) { setCursor(layout.total); return; }
    if (!seen) { setCursor(0); return; }
    let raf = 0;
    const t0 = performance.now();
    const step = () => {
      const c = cursorAt(performance.now() - t0, layout.total);
      setCursor(c);
      if (c < layout.total) raf = requestAnimationFrame(step);
    };
    setCursor(0);
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [layout, take, seen]);

  if (!run) return <p className="muted">No workflow record for this overlay.</p>;
  const src = run.skipped ? { label: `Skipped: ${run.skipped}`, cls: "data" } : RUN_SOURCE[run.source ?? ""];
  const head = (
    <div className="tr-head">
      {src && <span className={`badge ${src.cls}`}>{src.label}</span>}
      {run.ms != null && <span className="tr-total">{secs(run.ms)} end to end</span>}
      {run.resumed_from && <span className="badge resumed" title={`Checkpoint ${run.resumed_from}`}>↻ Resumed from checkpoint</span>}
    </div>
  );

  if (!layout) {
    const personas = Object.entries(run.personas ?? {});
    return (
      <div className="trace">
        {head}
        {personas.length > 0 && (
          <ul className="tr-personas">
            {personas.map(([p, s]) => <li key={p}><span>{personaLabel(p)}</span><span className={`src-${s}`}>{s === "agent" ? "AI · verified" : s}</span></li>)}
          </ul>
        )}
        <p className="muted">This run was recorded before the workflow kept its step timings.</p>
      </div>
    );
  }

  const playing = cursor < layout.total;
  const bars = layout.lanes.flatMap((l) => l.bars);
  const rejected = bars.filter((b) => b.tone === "rejected");
  const fellBack = bars.filter((b) => b.tone === "fallback" || b.tone === "failed");
  return (
    <div className="trace" ref={root}>
      {head}
      <div className="tr-status" aria-live="off">
        <span>{playing ? "Replaying the recorded run" : "Recorded run"}</span>
        <span className="tr-clock">{secs(cursor)} / {secs(layout.total)}</span>
        <button className="tr-replay" onClick={() => setTake((n) => n + 1)} aria-label="Replay the run">↻ Replay</button>
      </div>
      <div className="tr-lanes" role="img" aria-label={`Agent trace: ${bars.map(describe).join("; ")}`}>
        {layout.lanes.map((lane) => (
          <div key={lane.id} className={lane.persona ? "tr-lane persona" : "tr-lane"}>
            <span className="tr-label">{lane.label}</span>
            <div className="tr-track">
              {lane.bars.map((b, i) => {
                const d = drawn(b, cursor);
                const kind = MODEL_STEPS.has(b.step) ? "model" : "code";
                if (b.step === "verifier") {
                  return (
                    <span key={i} className={`tr-mark t-${b.tone} ${d > 0 ? "on" : ""}`} style={{ left: pct(b.start, layout.total) }} title={describe(b)}>
                      {b.tone === "rejected" ? "✕" : b.tone === "ok" ? "✓" : "!"}
                    </span>
                  );
                }
                return (
                  <span key={i} className={`tr-bar k-${kind} t-${b.tone}`} title={describe(b)}
                        style={{ left: pct(b.start, layout.total), width: pct(b.end - b.start, layout.total) }}>
                    <span className="tr-fill" style={{ transform: `scaleX(${d})` }} />
                    {b.attempt > 0 && d > 0.2 && <span className="tr-tag">retry</span>}
                  </span>
                );
              })}
            </div>
          </div>
        ))}
        <div className="tr-cursor-rail" aria-hidden="true">
          <span className={playing ? "tr-cursor on" : "tr-cursor"} style={{ left: pct(cursor, layout.total) }} />
        </div>
      </div>
      <div className="tr-axis" aria-hidden="true"><span>0 s</span><span>{secs(layout.total / 2)}</span><span>{secs(layout.total)}</span></div>
      <ul className="tr-legend">
        <li><i className="sw model" />Model call</li><li><i className="sw code" />Code step</li>
        <li><i className="sw ok">✓</i>Verifier passed</li><li><i className="sw rejected">✕</i>Verifier rejected</li>
        <li><i className="sw fallback" />Template fallback</li>
      </ul>
      {(rejected.length > 0 || fellBack.length > 0) && (
        <ul className="tr-notes">
          {rejected.map((b, i) => {
            const retried = bars.find((r) => r.step === "verifier" && r.persona === b.persona && r.attempt > b.attempt);
            return (
              <li key={`r${i}`} className={`tr-note ${lit(cursor, b)}`}>
                <b className="neg">✕ Verifier rejected the {personaLabel(b.persona ?? "")} draft</b>
                <span>{b.detail ?? "A number in the draft wasn't in the evidence."}</span>
                {retried && <span className={retried.tone === "ok" ? "pos" : "warn"}>
                  {retried.tone === "ok" ? "✓ Rewritten with that feedback and passed on retry" : "Retry failed: a template aired instead"}
                </span>}
              </li>
            );
          })}
          {fellBack.map((b, i) => (
            <li key={`f${i}`} className={`tr-note ${lit(cursor, b)}`}>
              <b className="warn">{b.persona ? personaLabel(b.persona) : STEP_VERB[b.step]}: template aired</b>
              <span>{b.detail ?? b.outcome}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const lit = (cursor: number, b: Bar) => (drawn(b, cursor) > 0 ? "on" : "");
