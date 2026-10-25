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


def _focus_player(m: Moment) -> str | None:
    """The player a scout note is about: the one in the headline (the shooter), else the first involved."""
    people = [m.names[p] for p in m.player_ids if p in m.names]
    return next((n for n in people if m.headline.startswith(n)), people[0] if people else None)


def _recap_numbers(m: Moment):
    home, away = m.names.get("h", "Home"), m.names.get("a", "Away")
    xh, xa = _ev(m, "xg_home"), _ev(m, "xg_away")
    better = home if float(xh or 0) > float(xa or 0) else away
    return home, away, _ev(m, "score", ""), xh, xa, better


def _academy(m: Moment, lang: str) -> str:
    team = m.team_name or ""
    k = m.kind
    if k in ("goal", "big_chance"):
        passes, dur = _ev(m, "chain_passes"), _ev(m, "chain_duration_s")
        if _ev(m, "started_from", "") == "turnover" and int(passes) <= 1 and not dur:
            return {"es": f"Recuperar y atacar: {team} disparó nada más robar. En el entrenamiento, mira hacia delante al recuperar.",
                    "ja": f"奪ったら前へ。{team}はボールを奪ってすぐにシュート。練習では、奪った瞬間に前を見よう。"}.get(
                lang, f"Win it and go: {team} shot the moment they won the ball. In training, look forward as soon as you get it back.")
        if _ev(m, "started_from", "") == "turnover" and int(passes) <= 1:
            return {"es": f"Recuperar y atacar: {team} disparó {dur} s después de robar. En el entrenamiento, mira hacia delante nada más recuperar.",
                    "ja": f"奪ったら前へ。{team}はボール奪取から{dur}秒でシュート。練習では、奪った瞬間に前を見よう。"}.get(
                lang, f"Win it and go: {team} shot {dur} s after winning the ball. In training, look forward the moment you get it back.")
        return {"es": f"La paciencia premia: {team} encadenó {passes} pases antes del disparo. Practica mover el balón hasta que se abra el espacio.",
                "ja": f"我慢のパス回し。{team}は{passes}本のパスでシュートまで運んだ。スペースが空くまでボールを動かす練習をしよう。"}.get(
            lang, f"Patience pays: {team} strung {passes} passes together before the shot. Practise moving the ball until the space opens.")
    if k == "momentum_swing":
        return {"es": f"{team} manda porque sigue atacando los espacios. Lección: cuando tu equipo domina, mantén el ritmo alto.",
                "ja": f"{team}がスペースを突き続けて主導権を握っている。優勢な時こそテンポを落とさないこと。"}.get(
            lang, f"{team} are on top because they keep attacking the space. Lesson: when your team has the upper hand, keep the tempo high.")
    if k == "pressing_surge":
        ppda = _ev(m, "ppda_8m")
        return {"es": f"{team} presiona en bloque: PPDA de {ppda}. Lección: presiona en equipo; el jugador más cercano da la señal.",
                "ja": f"{team}が連動してプレス(PPDA {ppda})。一番近い選手が合図を出し、チーム全体で奪いに行こう。"}.get(
            lang, f"{team} press as one: PPDA down to {ppda}. Lesson: press as a unit, and let the nearest player set the trigger.")
    home, away, score, xh, xa, better = _recap_numbers(m)
    return {"es": f"{home} {score} {away}. {better} generó mejores ocasiones (xG {xh} a {xa}). Lección: las buenas ocasiones nacen de buenas posiciones.",
            "ja": f"{home} {score} {away}。{better}が好機で上回った(xG {xh}対{xa})。良いシュートは良いポジションから生まれる。"}.get(
        lang, f"{home} {score} {away}. {better} made the better chances ({xh} vs {xa} xG). Lesson: good chances come from good positions.")


def _scout(m: Moment, lang: str) -> str:
    team = m.team_name or ""
    k = m.kind
    have = {e.key for e in m.evidence}
    if k in ("goal", "big_chance"):
        who, xg = _focus_player(m) or team, _ev(m, "xg")
        speed = _ev(m, "shot_speed_kmh") if "shot_speed_kmh" in have else None
        if lang == "es":
            body = (f"{who} convirtió una ocasión de xG {xg}." if k == "goal"
                    else f"{who} llegó a una ocasión de xG {xg} sin acertar.")
            body += f" Disparo a {speed} km/h." if speed else ""
            return body + (" A destacar: el desmarque y la definición." if k == "goal" else " Lo positivo: el movimiento para generarla.")
        if lang == "ja":
            body = f"{who}がxG {xg}の決定機を決めた。" if k == "goal" else f"{who}がxG {xg}の決定機に絡むも決めきれず。"
            body += f"シュート速度{speed} km/h。" if speed else ""
            return body + ("動き出しとフィニッシュに注目。" if k == "goal" else "チャンスを作った動きは評価できる。")
        body = f"{who} converted a {xg} xG chance." if k == "goal" else f"{who} got on the end of a {xg} xG chance but didn't convert."
        body += f" Shot at {speed} km/h." if speed else ""
        return body + (" Note the timing of the run and a clean strike." if k == "goal" else " The movement to create it is the positive.")
    if k == "momentum_swing":
        threat, opp = _ev(m, "threat_10m"), _ev(m, "opp_threat_10m")
        return {"es": f"Bloque de {team}: amenaza {threat} frente a {opp} en 10 minutos. Su línea ofensiva gana los duelos.",
                "ja": f"{team}の攻撃陣が直近10分で脅威度{threat}対{opp}。前線が局面で優位に立っている。"}.get(
            lang, f"{team} attack: {threat} threat to {opp} over 10 minutes. The front line is winning its duels.")
    if k == "pressing_surge":
        ppda = _ev(m, "ppda_8m")
        return {"es": f"Presión de {team}: PPDA {ppda} en 8 minutos. Muestra intensidad y coordinación de los delanteros.",
                "ja": f"{team}の前線が直近8分でPPDA {ppda}。運動量と連動したプレスが光る。"}.get(
            lang, f"{team} front line: PPDA {ppda} over 8 minutes. Shows work rate and coordinated triggers from the forwards.")
    home, away, score, xh, xa, _ = _recap_numbers(m)
    who = _focus_player(m)
    if who and "top_speed_kmh" in have:
        speed = _ev(m, "top_speed_kmh")
        return {"es": f"{home} {score} {away}. Físico destacado: {who}, velocidad máxima {speed} km/h.",
                "ja": f"{home} {score} {away}。身体能力で目立ったのは{who}(最高速度{speed} km/h)。"}.get(
            lang, f"{home} {score} {away}. Standout athlete: {who}, top speed {speed} km/h.")
    return {"es": f"{home} {score} {away}. xG {xh} a {xa}.", "ja": f"{home} {score} {away}。xG {xh}対{xa}。"}.get(
        lang, f"{home} {score} {away}. Expected goals {xh} to {xa}.")


def _text(m: Moment, persona: str, lang: str) -> str:
    if persona == "academy":
        return _academy(m, lang)
    if persona == "scout":
        return _scout(m, lang)
    team = m.team_name or ""
    k = m.kind
    if k in ("goal", "big_chance"):
        xg, passes, dur = _ev(m, "xg"), _ev(m, "chain_passes"), _ev(m, "chain_duration_s")
        if _ev(m, "started_from", "") == "turnover" and int(passes) <= 1:
            pct = round(float(xg) * 100)
            if lang == "es":
                return f"Robo y disparo en {dur} s: xG {xg}." if persona == "analyst" else f"{team} recuperó el balón y disparó al instante. Probabilidad de gol: {pct} %."
            if lang == "ja":
                return f"ボール奪取から{dur}秒でシュート。xG {xg}。" if persona == "analyst" else f"{team}がボールを奪って即シュート。得点確率{pct}%。"
            return (f"Ball won and shot within {dur} s: {xg} xG from a turnover." if persona == "analyst"
                    else f"{team} won it back and went straight for goal. Chance of scoring: {pct}%.")
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
    home, away = m.names.get("h", "Home"), m.names.get("a", "Away")
    xh, xa, sh, sa = _ev(m, "xg_home"), _ev(m, "xg_away"), _ev(m, "shots_home"), _ev(m, "shots_away")
    if lang == "es":
        return f"{home} {score} {away}. xG {xh} a {xa}; tiros {sh} a {sa}."
    if lang == "ja":
        return f"{home} {score} {away}。xG {xh}対{xa}、シュート{sh}対{sa}。"
    if persona == "analyst":
        return f"{home} {score} {away}. Expected goals {xh} to {xa}, from {sh} and {sa} shots."
    better = home if float(xh or 0) > float(xa or 0) else away
    return f"{home} {score} {away}. {better} created the better chances ({xh} vs {xa} xG)."


KEYS = {
    "goal": ["xg", "chain_passes", "chain_duration_s", "started_from"],
    "big_chance": ["xg", "chain_passes", "chain_duration_s", "started_from"],
    "momentum_swing": ["threat_10m", "opp_threat_10m"],
    "pressing_surge": ["ppda_8m", "ppda_match"],
    "half_time": ["score", "xg_home", "xg_away", "shots_home", "shots_away"],
    "full_time": ["score", "xg_home", "xg_away", "shots_home", "shots_away"],
}


SCOUT_KEYS = {"goal": ["shot_speed_kmh"], "big_chance": ["shot_speed_kmh"],
              "half_time": ["top_speed_kmh"], "full_time": ["top_speed_kmh"]}


def fallback_explanation(m: Moment, required: list[tuple[str, str]]) -> Explanation:
    have = {e.key for e in m.evidence}

    def keys(persona: str) -> list[str]:
        want = KEYS[m.kind] + (SCOUT_KEYS.get(m.kind, []) if persona == "scout" else [])
        return [k for k in want if k in have] or [e.key for e in m.evidence[:2]]

    return Explanation(
        variants=[
            Variant(persona=p, lang=lang, title=TITLES[m.kind].get(lang, TITLES[m.kind]["en"]),
                    text=_text(m, p, lang), evidence_keys=keys(p))
            for p, lang in required
        ]
    )
