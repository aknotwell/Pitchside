import type { Cue } from "../types";

export function WhyDrawer({ cue, onClose }: { cue: Cue | null; onClose: () => void }) {
  if (!cue) return null;
  const model = cue.data.model as string | undefined;
  const reason = cue.data.fallbackReason as string | undefined;
  return (
    <aside className="drawer" role="dialog" aria-label="Evidence behind this overlay">
      <button className="close" onClick={onClose} aria-label="Close">×</button>
      <h3>Why this overlay?</h3>
      <p className="drawer-title">{cue.title}</p>
      <p>{cue.text}</p>
      <h4>Evidence (computed by code, not the model)</h4>
      <table>
        <tbody>
          {cue.evidence.metrics.map((m) => (
            <tr key={m.key}>
              <td>{m.label}</td>
              <td className="num">{String(m.value)}{m.unit ? ` ${m.unit}` : ""}
                {m.before != null && m.after != null ? <span className="delta"> ({m.before} → {m.after})</span> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl>
        <dt>Source</dt><dd>{cue.source}{model ? ` · ${model}` : ""}{cue.verified ? " · verifier passed" : ""}</dd>
        {reason && (<><dt>Fallback reason</dt><dd>{reason}</dd></>)}
        <dt>Events</dt><dd className="mono">{cue.evidence.event_ids.slice(-6).join(", ") || "—"}</dd>
        {cue.trace_id && (<><dt>Trace</dt><dd className="mono">{cue.trace_id}</dd></>)}
        <dt>Cue</dt><dd className="mono">{cue.id}</dd>
      </dl>
    </aside>
  );
}
