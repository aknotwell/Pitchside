import type { AskAnswer, Cue, Lang, MatchState, MatchSummary, Persona } from "./types";

export const API = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, init);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  matches: () => j<{ matches: MatchSummary[]; now: number }>("/api/matches"),
  match: (id: string) => j<MatchState>(`/api/matches/${id}`),
  cues: (id: string, persona: Persona, lang: Lang) =>
    j<{ cues: Cue[] }>(`/api/matches/${id}/cues?persona=${persona}&lang=${lang}`),
  negotiate: (id: string, persona: Persona, lang: Lang) =>
    j<{ url: string }>(`/api/negotiate?matchId=${encodeURIComponent(id)}&persona=${persona}&lang=${lang}`),
  start: (speed: number, periodMin: number) =>
    j<{ started: boolean; seed: number }>("/api/matches", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ speed, periodMin }),
    }),
  ask: (matchId: string, question: string, lang: Lang) =>
    j<AskAnswer>("/api/ask", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ matchId, question, lang }),
    }),
  replays: () => j<{ replays: { matchId: string; home: string; away: string; score: { h: number; a: number }; at: number }[] }>("/api/replays"),
  chaos: () => j<{ armed: boolean }>("/api/chaos", { method: "POST" }),
  ops: () => j<{ chaos: { armed: boolean; lastFiredAt?: number }; runs: Record<string, unknown>[] }>("/api/ops"),
};
