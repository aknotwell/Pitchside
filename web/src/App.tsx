import { lazy, Suspense, useEffect, useState } from "react";
import { api } from "./api";
import { isLive, MatchPicker } from "./components/MatchPicker";
import { Overlay } from "./components/Overlay";
import { Pitch } from "./components/Pitch";
import { Ask } from "./components/Ask";
import { Producer } from "./components/Producer";
import { RestartLabel } from "./components/RestartLabel";
import { Scoreboard } from "./components/Scoreboard";
import { Squads } from "./components/Squads";
import { Stats } from "./components/Stats";
import { Transport } from "./components/Transport";
import { WhyDrawer } from "./components/WhyDrawer";
import { BROADCAST_DELAY_S, useLiveMatch } from "./live";
import { useReplay } from "./replay";
import { LAB_META, labSample } from "./lab";
import type { Cue, Lang, MatchState, MatchSummary, Persona } from "./types";
import { PERSONAS } from "./types";
import type { CamMode } from "./components/Pitch3D";

const Pitch3D = lazy(() => import("./components/Pitch3D"));

function webglAvailable(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}
const HAS_GL = webglAvailable();
const CAMS: { id: CamMode; label: string }[] = [
  { id: "broadcast", label: "Broadcast" }, { id: "tactical", label: "Tactical" },
  { id: "follow", label: "Follow player" }, { id: "free", label: "Free (drag)" },
];

const LANGS: { id: Lang; label: string }[] = [{ id: "en", label: "EN" }, { id: "es", label: "ES" }, { id: "ja", label: "日本語" }];

export default function App() {
  const params = new URLSearchParams(location.search);
  if (params.get("lab")) return <AthleteLab cam={(params.get("cam") as CamMode) || "free"} follow={params.get("follow")} />;
  return <Main params={params} />;
}

function AthleteLab({ cam, follow }: { cam: CamMode; follow: string | null }) {
  return (
    <div className="app"><main style={{ gridTemplateColumns: "1fr" }}><section className="stage">
      <Suspense fallback={null}><Pitch3D sample={labSample} meta={LAB_META} follow={follow} camMode={cam} /></Suspense>
    </section></main></div>
  );
}

function Main({ params }: { params: URLSearchParams }) {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [matchesLoaded, setMatchesLoaded] = useState(false);
  const [matchId, setMatchId] = useState<string | null>(params.get("match"));
  const [persona, setPersona] = useState<Persona>(
    PERSONAS.find((p) => p.id === params.get("persona"))?.id ?? "casual");
  const [lang, setLang] = useState<Lang>((params.get("lang") as Lang) || "en");
  const [why, setWhy] = useState<Cue | null>(null);
  const [tab, setTab] = useState<"stats" | "squads" | "feed" | "ask" | "producer">("stats");
  const [follow, setFollow] = useState<string | null>(params.get("follow"));
  const [view, setView] = useState<"2d" | "3d">(params.get("view") === "2d" || !HAS_GL ? "2d" : "3d");
  const [cam, setCam] = useState<CamMode>((params.get("cam") as CamMode) || "broadcast");
  const [replays, setReplays] = useState<Awaited<ReturnType<typeof api.replays>>["replays"]>([]);
  const [replayId, setReplayId] = useState<string | null>(params.get("replay"));
  const [finalState, setFinalState] = useState<MatchState | null>(null);
  const liveFeed = useLiveMatch(replayId ? null : matchId, persona, lang);
  const replayFeed = useReplay(replayId, persona, lang, finalState);
  const live = replayId ? replayFeed : liveFeed;
  const { paused, controls } = live;

  useEffect(() => {
    api.replays().then((r) => setReplays(r.replays)).catch(() => {});
  }, []);
  useEffect(() => {
    if (replayId) api.match(replayId).then(setFinalState).catch(() => setFinalState(null));
  }, [replayId]);

  // Space toggles pause, unless focus is on something that uses Space itself
  // (the Ask box, buttons, links, the seek bar, the recap audio player).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== " " || e.repeat || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.isComposing || e.defaultPrevented) return;
      if (e.target instanceof Element && e.target.closest("input,textarea,select,button,a[href],audio,video,[contenteditable]")) return;
      e.preventDefault();
      controls.togglePause();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [controls]);

  useEffect(() => {
    let alive = true;
    const load = () => api.matches().then((r) => {
      if (!alive) return;
      setMatches(r.matches);
      setMatchesLoaded(true);
      setMatchId((cur) => cur ?? r.matches.find(isLive)?.id ?? null);
    }).catch(() => {});
    load();
    const id = setInterval(load, 10000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  useEffect(() => {  // nothing live: open the newest replay, so there's always something to watch
    if (matchesLoaded && !matchId && !replayId && replays.length) setReplayId(replays[0].matchId);
  }, [matchesLoaded, matchId, replayId, replays]);

  useEffect(() => {
    const q = new URLSearchParams({ ...(replayId ? { replay: replayId } : matchId ? { match: matchId } : {}), persona, lang, view, ...(view === "3d" ? { cam } : {}), ...(follow ? { follow } : {}) });
    history.replaceState(null, "", `?${q}`);
  }, [matchId, replayId, persona, lang, view, cam, follow]);

  return (
    <div className="app">
      <header>
        <h1>MatchLens <small>live explain layer</small></h1>
        <MatchPicker matches={matches} replays={replays} matchId={matchId} replayId={replayId}
                     onMatch={(id) => { setReplayId(null); setMatchId(id); }}
                     onReplay={setReplayId} />
        <div className="seg" role="group" aria-label="Viewer">
          {PERSONAS.map((p) => (
            <button key={p.id} className={persona === p.id ? "on" : ""} title={p.hint} onClick={() => setPersona(p.id)}>{p.label}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Language">
          {LANGS.map((l) => <button key={l.id} className={lang === l.id ? "on" : ""} onClick={() => setLang(l.id)}>{l.label}</button>)}
        </div>
      </header>
      <main>
        <div className="watch">
          <section className={view === "3d" ? "stage three" : "stage"}>
            <Scoreboard state={live.state} delay={replayId ? 0 : BROADCAST_DELAY_S} connected={live.connected} replay={!!replayId} paused={paused}
                        behind={!replayId && !live.followLive} />
            {view === "3d" ? (
              <Suspense fallback={<Pitch frame={live.frame} meta={live.state?.meta} cues={live.cues} follow={follow} />}>
                <Pitch3D sample={live.sample} meta={live.state?.meta} follow={follow} camMode={cam} paused={paused} />
              </Suspense>
            ) : (
              <Pitch frame={live.frame} sample={live.sample} meta={live.state?.meta} cues={live.cues} follow={follow} />
            )}
            <div className="viewbar">
              <div className="seg" role="group" aria-label="Pitch view">
                <button className={view === "2d" ? "on" : ""} onClick={() => setView("2d")}>2D</button>
                <button className={view === "3d" ? "on" : ""} onClick={() => setView("3d")} disabled={!HAS_GL}>3D</button>
              </div>
              {view === "3d" && (
                <select aria-label="Camera" value={cam} onChange={(e) => setCam(e.target.value as CamMode)}>
                  {CAMS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
              )}
            </div>
            <RestartLabel restart={live.frame?.restart} meta={live.state?.meta} />
            <Overlay cues={live.cues} onWhy={setWhy} />
          </section>
          <Transport live={live} isLive={!replayId} />
        </div>
        <aside className="side">
          <nav className="tabs">
            {(["stats", "squads", "feed", "ask", "producer"] as const).map((t) => (
              <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t === "feed" ? "Moments" : t[0].toUpperCase() + t.slice(1)}</button>
            ))}
          </nav>
          {tab === "stats" && <Stats state={live.state} persona={persona} />}
          {tab === "squads" && (
            <Squads meta={live.state?.meta} selected={follow} onSelect={setFollow}
                    onPitch={new Set(live.frame?.players.map((p) => p.id) ?? [])} />
          )}
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
