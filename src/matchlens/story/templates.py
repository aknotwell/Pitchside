"""Deterministic, localized fallback explanations.

Used when the model is unavailable, too slow, or its draft fails verification
twice. Built only from the evidence pack, so it is always correct.
"""

from __future__ import annotations

from matchlens.contracts import Moment
from matchlens.story.verifier import Explanation, Variant


def _ev(m: Moment, key: str, default: float | str = 0):
    for e in m.evidence:
        if e.key == key:
            return e.value
    return default


TITLES = {
    "goal": {"en": "Why the goal came", "es": "Por qué llegó el gol", "ja": "ゴールの理由"},
    "big_chance": {"en": "Big chance", "es": "Ocasión clarísima", "ja": "決定機"},
    "momentum_swing": {"en": "Momentum shift", "es": "Cambio de dinámica", "ja": "流れが変わった"},
    "pressing_surge": {"en": "Pressing surge", "es": "Presión al máximo", "ja": "プレス強化"},
    "half_time": {"en": "Half-time story", "es": "Resumen del descanso", "ja": "前半のまとめ"},
    "full_time": {"en": "Full-time story", "es": "Resumen final", "ja": "試合のまとめ"},
}


def _text(m: Moment, persona: str, lang: str) -> str:
    team = m.team_name or ""
    k = m.kind
    if k in ("goal", "big_chance"):
        xg, passes, dur = _ev(m, "xg"), _ev(m, "chain_passes"), _ev(m, "chain_duration_s")
        if lang == "es":
            return (f"Disparo con xG {xg}: una jugada de {passes} pases en {dur} s." if persona == "analyst"
                    else f"{team} lo construyó con {passes} pases. Probabilidad de gol: {round(float(xg) * 100)} %.")
        if lang == "ja":
            return (f"xG {xg}のシュート。{passes}本のパス、{dur}秒の攻撃。" if persona == "analyst"
                    else f"{team}が{passes}本のパスでつないだ。得点確率{round(float(xg) * 100)}%。")
        return (f"Shot worth {xg} xG at the end of a {passes}-pass, {dur} s move." if persona == "analyst"
                else f"{team} worked it through {passes} passes. Chance of scoring: {round(float(xg) * 100)}%.")
    if k == "momentum_swing":
        threat, opp = _ev(m, "threat_10m"), _ev(m, "opp_threat_10m")
        if lang == "es":
            return f"{team} domina los últimos 10 minutos: amenaza {threat} frente a {opp}."
        if lang == "ja":
            return f"直近10分は{team}が優勢。脅威度{threat}対{opp}。"
        return (f"{team} lead the last 10 minutes on threat, {threat} to {opp}." if persona == "analyst"
                else f"{team} are on top right now and creating more danger.")
    if k == "pressing_surge":
        ppda, before = _ev(m, "ppda_8m"), _ev(m, "ppda_match")
        if lang == "es":
            return f"{team} presiona arriba: PPDA {ppda} en 8 minutos, antes {before}."
        if lang == "ja":
            return f"{team}が前線からプレス。直近8分のPPDAは{ppda}(それまで{before})。"
        return (f"{team} are pressing higher: PPDA {ppda} over 8 minutes, down from {before}." if persona == "analyst"
                else f"{team} are hunting the ball high up the pitch and forcing mistakes.")
    score = _ev(m, "score", "")
    if lang == "es":
        return f"Marcador {score}. xG {_ev(m, 'xg_home')} a {_ev(m, 'xg_away')}."
    if lang == "ja":
        return f"スコア{score}。xG {_ev(m, 'xg_home')}対{_ev(m, 'xg_away')}。"
    return f"It's {score}. Expected goals {_ev(m, 'xg_home')} to {_ev(m, 'xg_away')}."


KEYS = {
    "goal": ["xg", "chain_passes", "chain_duration_s"],
    "big_chance": ["xg", "chain_passes", "chain_duration_s"],
    "momentum_swing": ["threat_10m", "opp_threat_10m"],
    "pressing_surge": ["ppda_8m", "ppda_match"],
    "half_time": ["score", "xg_home", "xg_away"],
    "full_time": ["score", "xg_home", "xg_away"],
}


def fallback_explanation(m: Moment, required: list[tuple[str, str]]) -> Explanation:
    return Explanation(
        variants=[
            Variant(persona=p, lang=lang, title=TITLES[m.kind].get(lang, TITLES[m.kind]["en"]),
                    text=_text(m, p, lang), evidence_keys=KEYS[m.kind])
            for p, lang in required
        ]
    )
