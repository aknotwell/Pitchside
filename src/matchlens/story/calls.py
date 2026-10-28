"""Spoken calls for the player's sound-on mode: which template cues are read aloud, how, and in what words.

Template cues (``lang`` "all") carry English titles, so each call is rebuilt from the cue's own
``data`` in the viewer's language rather than read from its text."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Call:
    """How one template is voiced: a phrase per language and the prosody it is read with."""

    phrases: dict[str, str]
    rate: str = "+5%"
    pitch: str = "+0%"


TO = {"en": " to ", "es": " a ", "ja": "対"}

CALLS: dict[str, Call] = {
    "goal": Call({"en": "Goal! {team}! {player} scores. It's {score}.",
                  "es": "¡Gol! ¡Gol de {team}! Marca {player}. {score}.",
                  "ja": "ゴール！{team}、{player}！{score}。"}, rate="+20%", pitch="+15%"),
    "card": Call({"en": "Yellow card. {player}, {team}.",
                  "es": "Tarjeta amarilla para {player}, de {team}.",
                  "ja": "イエローカード。{team}の{player}。"}),
    "substitution": Call({"en": "{team} substitution. {on} comes on for {off}.",
                          "es": "Cambio en {team}. Entra {on}, sale {off}.",
                          "ja": "{team}、選手交代。{off}に代わって{on}が入ります。"}),
}


def spoken(cue: dict, lang: str) -> tuple[str, Call] | None:
    """The words to say for a stored cue in `lang`, or None when its template isn't announced."""
    call = CALLS.get(cue.get("template", ""))
    if call is None:
        return None
    d = cue.get("data") or {}
    team = str(cue.get("title", "")).split("·")[-1].strip()
    score = str(d.get("score", "")).replace("–", TO.get(lang, " to "))
    player = d.get("player", "")
    return call.phrases.get(lang, call.phrases["en"]).format(
        team=team, player=team if player in ("", "?") else player, on=d.get("on", ""), off=d.get("off", ""), score=score), call
