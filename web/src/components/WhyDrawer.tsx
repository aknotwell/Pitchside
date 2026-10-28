import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Cue, Lang, MomentDetail, Persona } from "../types";
import { PERSONAS } from "../types";
import { AgentTrace } from "./AgentTrace";
import { badge } from "./Overlay";

/** The moment's copy for each viewer in `lang`, in the player's viewer order; null where none aired. */
export function audiences(cues: Cue[], lang: Lang): { persona: Persona; label: string; hint: string; cue: Cue | null }[] {
  return PERSONAS.map((p) => ({ persona: p.id, label: p.label, hint: p.hint, cue: cues.find((c) => c.persona === p.id && c.lang === lang) ?? null }));
}

/** Agent cues name their moment; the GOAL data card carries its moment's id as its own id. Other data cues have none. */
export const momentOf = (c: Cue): string | null => c.moment_id ?? (c.template === "goal" ? c.id : null);

const RETRY_MS = 2000, RETRIES = 30;

type Load = { status: "loading" } | { status: "ready"; detail: MomentDetail } | { status: "missing" };

export function WhyDrawer({ cue, persona, lang, onPersona, onClose }: {
  cue: Cue | null; persona: Persona; lang: Lang; onPersona: (p: Persona) => void; onClose: () => void;
}) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const heading = useRef<HTMLHeadingElement>(null);
  const drawer = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const moment = cue ? momentOf(cue) : null;
  const key = cue ? `${cue.matchId}/${moment ?? cue.id}` : null;
  const open = cue !== null;

  useEffect(() => {
    if (!cue) return;
    if (!moment) { setLoad({ status: "missing" }); return; }
    let alive = true, timer = 0;
    setLoad({ status: "loading" });
    // On a live match the GOAL card airs before the agents finish: keep asking until their run lands.
    const fetchDetail = (left: number) => api.moment(cue.matchId, moment)
      .then((detail) => {
        if (!alive) return;
        if (!detail || !Array.isArray(detail.cues)) { setLoad({ status: "missing" }); return; }
        setLoad({ status: "ready", detail });
        if (!detail.run && left > 0) timer = window.setTimeout(() => fetchDetail(left - 1), RETRY_MS);
      })
      .catch(() => {
        if (!alive) return;
        setLoad((l) => (l.status === "ready" ? l : { status: "missing" }));
        if (left > 0) timer = window.setTimeout(() => fetchDetail(left - 1), RETRY_MS);
      });
    fetchDetail(RETRIES);
    return () => { alive = false; clearTimeout(timer); };
  }, [key]);

  useEffect(() => {  // focus moves into the drawer and stays there until Escape, and then goes back where it was
    if (!open) return;
    const back = document.activeElement as HTMLElement | null;
    heading.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close.current(); return; }
      if (e.key !== "Tab" || !drawer.current) return;
      const stops = [...drawer.current.querySelectorAll<HTMLElement>("button:not([disabled]),a[href],[tabindex='0']")];
      const first = stops[0], last = stops[stops.length - 1];
      const at = document.activeElement;
      if (e.shiftKey && (at === first || at === heading.current)) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && (at === last || !drawer.current.contains(at))) { e.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); back?.focus?.(); };
  }, [open]);

  if (!cue) return null;
  const detail = load.status === "ready" ? load.detail : null;
  const cards = audiences(detail?.cues ?? [], lang);
  const written = cards.some((c) => c.cue);
  const shown = cards.find((c) => c.persona === persona)?.cue ?? cue;
  const model = shown.data.model as string | undefined;
  const reason = shown.data.fallbackReason as string | undefined;
  const minute = `${Math.floor(cue.display_at_s / 60)}′`;

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" ref={drawer} role="dialog" aria-modal="true" aria-labelledby="drawer-h">
        <button className="close" onClick={onClose} aria-label="Close">×</button>
        <p className="kicker">Behind this moment · {minute}</p>
        <h3 id="drawer-h" ref={heading} tabIndex={-1}>{cue.title}</h3>

        <section className="dsec">
          <h4>One moment, four audiences</h4>
          {load.status === "loading" && <div className="aud-grid">{PERSONAS.map((p) => <div key={p.id} className="aud skeleton" />)}</div>}
          {load.status !== "loading" && written && (
            <div className="aud-grid">
              {cards.map((c) => (
                <button key={c.persona} className={`aud ${c.persona === persona ? "on" : ""} ${c.cue ? "" : "none"}`}
                        aria-pressed={c.persona === persona} onClick={() => onPersona(c.persona)} title={c.hint}>
                  <span className="aud-who">{c.label}{c.persona === persona && <span className="aud-you">watching</span>}
                    {c.cue && <span className="aud-badge">{badge(c.cue)}</span>}</span>
                  {c.cue ? (<><span className="aud-title">{c.cue.title}</span><span className="aud-text">{c.cue.text}</span></>)
                    : <span className="aud-text muted">No {c.label} version aired for this moment.</span>}
                </button>
              ))}
            </div>
          )}
          {load.status !== "loading" && !written && (
            <div className="aud-grid one">
              <div className="aud on">
                <span className="aud-who">{cue.persona === "all" ? "Every viewer" : PERSONAS.find((p) => p.id === cue.persona)?.label}
                  <span className="aud-badge">{badge(cue)}</span></span>
                <span className="aud-title">{cue.title}</span><span className="aud-text">{cue.text}</span>
              </div>
            </div>
          )}
        </section>

        <section className="dsec">
          <h4>Agent trace</h4>
          {load.status === "loading" ? <div className="trace skeleton tall" />
            : detail?.run ? <AgentTrace run={detail.run} />
            : detail?.moment ? <p className="muted pending">The agents are still writing this moment. Their run appears here when it lands.</p>
            : <p className="muted">{cue.source === "template"
                ? "Computed straight from the event stream by code: no agent run behind this overlay."
                : "The workflow record for this moment isn't available here."}</p>}
        </section>

        <section className="dsec">
          <h4>Evidence (computed by code, not the model)</h4>
          <table>
            <tbody>
              {shown.evidence.metrics.map((m) => (
                <tr key={m.key}>
                  <td>{m.label}</td>
                  <td className="num">{String(m.value)}{m.unit ? ` ${m.unit}` : ""}
                    {m.before != null && m.after != null ? <span className="delta"> ({m.before} → {m.after})</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <dl>
            <dt>Source</dt><dd>{shown.source}{model ? ` · ${model}` : ""}{shown.verified ? " · verifier passed" : ""}</dd>
            {reason && (<><dt>Fallback reason</dt><dd>{reason}</dd></>)}
            <dt>Events</dt><dd className="mono">{shown.evidence.event_ids.slice(-6).join(", ") || "—"}</dd>
            {shown.trace_id && (<><dt>Trace</dt><dd className="mono">{shown.trace_id}</dd></>)}
            <dt>Cue</dt><dd className="mono">{shown.id}</dd>
          </dl>
        </section>
      </aside>
    </>
  );
}
