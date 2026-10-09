import { useState } from "react";
import { api } from "../api";
import type { Lang } from "../types";

const SUGGEST: Record<Lang, string[]> = {
  en: ["Who has been the better team and why?", "Who is the fastest player?", "What changed in the second half?"],
  es: ["¿Quién ha sido mejor y por qué?", "¿Quién es el jugador más rápido?"],
  ja: ["どちらのチームが優勢？その理由は？", "一番速い選手は誰？"],
};

export function Ask({ matchId, lang }: { matchId: string | null; lang: Lang }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<{ q: string; a: string; tools?: string[] }[]>([]);

  const send = async (question: string) => {
    if (!matchId || !question.trim()) return;
    setBusy(true);
    try {
      const r = await api.ask(matchId, question, lang);
      setLog((l) => [{ q: question, a: r.answer, tools: r.tools }, ...l]);
      setQ("");
    } catch (e) {
      setLog((l) => [{ q: question, a: `⚠ ${(e as Error).message}` }, ...l]);
    } finally { setBusy(false); }
  };

  return (
    <div className="ask">
      <h3>Ask the match</h3>
      <p className="muted">An Agent Framework agent answers through the MatchLens MCP server (Azure Functions). It only uses numbers the tools return.</p>
      <form onSubmit={(e) => { e.preventDefault(); void send(q); }} className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={300} placeholder="Ask about this match…" aria-label="Question" />
        <button disabled={busy || !matchId}>{busy ? "…" : "Ask"}</button>
      </form>
      <div className="chips">{SUGGEST[lang].map((s) => <button key={s} onClick={() => void send(s)} disabled={busy}>{s}</button>)}</div>
      <ul className="answers">
        {log.map((x, i) => (
          <li key={i}><b>{x.q}</b><p>{x.a}</p>{x.tools && x.tools.length > 0 && <span className="muted">tools: {x.tools.join(", ")}</span>}</li>
        ))}
      </ul>
    </div>
  );
}
