import { useEffect, useState } from "react";
import { api } from "./api";
import { Overlay } from "./components/Overlay";
import { Pitch } from "./components/Pitch";
import { Ask } from "./components/Ask";
import { Producer } from "./components/Producer";
import { Scoreboard } from "./components/Scoreboard";
import { Stats } from "./components/Stats";
import { WhyDrawer } from "./components/WhyDrawer";
import { BROADCAST_DELAY_S, useLiveMatch } from "./live";
import type { Cue, Lang, MatchSummary, Persona } from "./types";

const LANGS: { id: Lang; label: string }[] = [{ id: "en", label: "EN" }, { id: "es", label: "ES" }, { id: "ja", label: "日本語" }];

export default function App() {
  const params = new URLSearchParams(location.search);
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [matchId, setMatchId] = useState<string | null>(params.get("match"));
  const [persona, setPersona] = useState<Persona>((params.get("persona") as Persona) || "casual");
  const [lang, setLang] = useState<Lang>((params.get("lang") as Lang) || "en");
  const [why, setWhy] = useState<Cue | null>(null);
  const [tab, setTab] = useState<"stats" | "feed" | "ask" | "producer">("stats");
  const live = useLiveMatch(matchId, persona, lang);

  useEffect(() => {
    let alive = true;
    const load = () => api.matches().then((r) => {
      if (!alive) return;
      setMatches(r.matches);
      setMatchId((cur) => cur ?? (r.matches.find((m) => !m.ended) ?? r.matches[0])?.id ?? null);
    }).catch(() => {});
    load();
    const id = setInterval(load, 10000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    const q = new URLSearchParams({ ...(matchId ? { match: matchId } : {}), persona, lang });
    history.replaceState(null, "", `?${q}`);
  }, [matchId, persona, lang]);

  return (
    <div className="app">
      <header>
        <h1>MatchLens <small>live explain layer</small></h1>
        <select aria-label="Match" value={matchId ?? ""} onChange={(e) => setMatchId(e.target.value || null)}>
          {matches.length === 0 && <option value="">No matches yet</option>}
          {matches.map((m) => (
            <option key={m.id} value={m.id}>{m.home} {m.homeGoals}–{m.awayGoals} {m.away} · {m.ended ? "FT" : m.displayClock}</option>
          ))}
        </select>
        <div className="seg" role="group" aria-label="Viewer">
          {(["casual", "analyst"] as Persona[]).map((p) => (
            <button key={p} className={persona === p ? "on" : ""} onClick={() => setPersona(p)}>{p === "casual" ? "Fan" : "Analyst"}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Language">
          {LANGS.map((l) => <button key={l.id} className={lang === l.id ? "on" : ""} onClick={() => setLang(l.id)}>{l.label}</button>)}
        </div>
      </header>
      <main>
        <section className="stage">
          <Scoreboard state={live.state} delay={BROADCAST_DELAY_S} connected={live.connected} />
          <Pitch frame={live.frame} meta={live.state?.meta} cues={live.cues} />
          <Overlay cues={live.cues} onWhy={setWhy} />
        </section>
        <aside className="side">
          <nav className="tabs">
            {(["stats", "feed", "ask", "producer"] as const).map((t) => (
              <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t === "feed" ? "Moments" : t[0].toUpperCase() + t.slice(1)}</button>
            ))}
          </nav>
          {tab === "stats" && <Stats state={live.state} persona={persona} />}
          {tab === "feed" && (
            <ul className="feed">
              {live.history.map((c) => (
                <li key={c.id} onClick={() => setWhy(c)} className={`src-${c.source}`}>
                  <span className="t">{Math.floor(c.display_at_s / 60)}′</span> <b>{c.title}</b><br /><span>{c.text}</span>
                </li>
              ))}
              {live.history.length === 0 && <li className="muted">Moments appear here as the match unfolds.</li>}
            </ul>
          )}
          {tab === "ask" && <Ask matchId={matchId} lang={lang} />}
          {tab === "producer" && <Producer />}
        </aside>
      </main>
      <WhyDrawer cue={why} onClose={() => setWhy(null)} />
      <footer>
        Synthetic data only: a fictional league, clubs and players. Overlays marked "AI" are model-written and code-verified.
        Not affiliated with any league or club.
      </footer>
    </div>
  );
}
