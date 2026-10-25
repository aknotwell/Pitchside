import { useState } from "react";
import { api } from "../api";
import type { AskAnswer, Lang } from "../types";

const SUGGEST: Record<Lang, string[]> = {
  en: ["Who has been the better team and why?", "Who is the fastest player?", "What changed in the second half?"],
  es: ["¿Quién ha sido mejor y por qué?", "¿Quién es el jugador más rápido?"],
  ja: ["どちらのチームが優勢？その理由は？", "一番速い選手は誰？"],
};

export function Ask({ matchId, lang }: { matchId: string | null; lang: Lang }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<{ q: string; r?: AskAnswer; err?: string; ms: number }[]>([]);

  const send = async (question: string) => {
    if (!matchId || !question.trim()) return;
    setBusy(true);
    const t0 = performance.now();
    try {
      const r = await api.ask(matchId, question, lang);
      setLog((l) => [{ q: question, r, ms: performance.now() - t0 }, ...l]);
      setQ("");
    } catch (e) {
      setLog((l) => [{ q: question, err: `⚠ ${(e as Error).message}`, ms: performance.now() - t0 }, ...l]);
    } finally { setBusy(false); }
  };

  return (
    <div className="ask">
      <h3>Ask the match</h3>
      <p className="muted">Reflex answers the common questions in about a second: a batched classifier routes the question and code writes the numbers. Everything else goes to an Agent Framework agent on the MatchLens MCP server, which starts at the same time.</p>
      <form onSubmit={(e) => { e.preventDefault(); void send(q); }} className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={300} placeholder="Ask about this match…" aria-label="Question" />
        <button disabled={busy || !matchId}>{busy ? "…" : "Ask"}</button>
      </form>
      <div className="chips">{SUGGEST[lang].map((s) => <button key={s} onClick={() => void send(s)} disabled={busy}>{s}</button>)}</div>
      <ul className="answers">
        {log.map((x, i) => (
          <li key={i}><b>{x.q}</b><p>{x.r?.answer ?? x.err}</p>{x.r && <Route r={x.r} ms={x.ms} />}</li>
        ))}
      </ul>
    </div>
  );
}

function Route({ r, ms }: { r: AskAnswer; ms: number }) {
  const s = (ms / 1000).toFixed(1);
  const rx = r.reflex;
  if (rx && rx.outcome !== "pass") {
    const how = rx.outcome === "decline" ? "declined" : rx.tier === 0 ? `${rx.capability}, written by code` : `${rx.capability}, one model call, numbers checked`;
    return <span className="route reflex">⚡ Reflex · {s} s · {how}</span>;
  }
  return <span className="route agent">Agent · {s} s{r.tools.length > 0 && ` · MCP: ${r.tools.join(", ")}`}</span>;
}
