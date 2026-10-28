import pytest
from agent_framework import InMemoryCheckpointStorage

from matchlens.contracts import PERSONAS, EvidenceItem, Moment
from matchlens.story.verifier import Core, Explanation, Variant, verify
from matchlens.story.workflow import DEFAULT_SEGMENTS, Storyteller, TemplateWriter

SEGS = [("analyst", "en"), ("casual", "es")]


class FakeWriter:
    """Stands in for Foundry. ``persona_fn(m, persona, langs, feedback)`` returns an Explanation."""

    def __init__(self, persona_fn, core_fn=None):
        self.persona_fn = persona_fn
        self.core_fn = core_fn or (lambda m: Core(angle="Ironvale Athletic press higher.", evidence_keys=["ppda_8m"]))
        self.core_calls, self.persona_calls = [], []

    async def core(self, m):
        self.core_calls.append(m.moment_id)
        return self.core_fn(m), "fake-core"

    async def persona(self, m, persona, langs, core, feedback):
        self.persona_calls.append((persona, tuple(langs), core, list(feedback)))
        return self.persona_fn(m, persona, langs, feedback), "fake-model"


def writes_good(m, persona, langs, fb):
    return good(m, [(persona, lang) for lang in langs])


def moment(kind="pressing_surge", clock=3300.0):
    return Moment(
        moment_id=f"m1-e00100-{kind}", match_id="m1", kind=kind, clock_s=clock, period=2,
        team_id="a", team_name="Ironvale Athletic", names={"a": "Ironvale Athletic", "h": "Harbour City"},
        headline="Ironvale Athletic turn up the press",
        evidence=[EvidenceItem(key="ppda_8m", label="PPDA 8m", value=4.5, before=11.7, after=4.5),
                  EvidenceItem(key="ppda_match", label="PPDA match", value=11.7),
                  EvidenceItem(key="opp_pass_acc", label="pass accuracy", value=0.74)],
        score={"h": 1, "a": 0},
    )


def good(m, segs):
    return Explanation(variants=[
        Variant(persona=p, lang=lang, title="Press on", text="PPDA fell from 11.7 to 4.5; passing at 74%.",
                evidence_keys=["ppda_8m", "ppda_match"]) for p, lang in segs])


def test_verifier_accepts_grounded_text():
    m = moment()
    assert verify(good(m, SEGS), m, SEGS, {"Zeki Zelenko"}).ok


def test_verifier_rejects_invented_numbers_names_and_keys():
    m = moment()
    bad = Explanation(variants=[
        Variant(persona="analyst", lang="en", title="Press", text="PPDA is 3.1 and Zelenko is everywhere. Bet now!",
                evidence_keys=["made_up"])])
    r = verify(bad, m, SEGS, {"Zeki Zelenko"})
    assert not r.ok
    text = " ".join(r.problems)
    for needle in ("3.1", "Zeki Zelenko", "made_up", "casual lang=es", "betting"):
        assert needle in text


def test_verifier_tells_a_long_draft_how_much_to_cut():
    m = moment()
    long = Explanation(variants=[Variant(persona=p, lang=lang, title="Press on", evidence_keys=["ppda_8m"],
                                         text="PPDA fell from 11.7 to 4.5. " + "x" * 280) for p, lang in SEGS])
    problems = verify(long, m, SEGS, set()).problems
    assert any("text is 308 characters, over the 260 limit: cut 78+" in p for p in problems)


async def run(story, m):
    return await story.tell(m)


def trace(info):
    return [(s["step"], s["persona"], s["attempt"], s["outcome"]) for s in info["steps"]]


async def test_agent_path_produces_verified_cues():
    writer = FakeWriter(writes_good)
    story = Storyteller(writer, roster=lambda _: set(), segments=SEGS)
    cues, info = await run(story, moment())
    assert info["source"] == "agent" and len(cues) == 2
    assert {c.lang for c in cues} == {"en", "es"}
    assert all(c.verified and c.evidence.metrics for c in cues)
    # one angle, then one call per persona with that persona's languages and the angle
    assert len(writer.core_calls) == 1
    assert sorted((p, langs) for p, langs, _, _ in writer.persona_calls) == [("analyst", ("en",)), ("casual", ("es",))]
    assert all(core is not None for _, _, core, _ in writer.persona_calls)


async def test_every_persona_and_language_by_default():
    cues, info = await run(Storyteller(FakeWriter(writes_good), roster=lambda _: set()), moment())
    assert {(c.persona, c.lang) for c in cues} == set(DEFAULT_SEGMENTS)
    assert set(info["personas"]) == set(PERSONAS) == {"analyst", "casual", "academy", "scout"}


async def test_retry_then_success():
    calls = []
    def persona_fn(m, persona, langs, fb):
        calls.append(fb)
        if len(calls) <= 2:  # both personas' first drafts invent a number
            return Explanation(variants=[Variant(persona=persona, lang=lang, title="x", text="PPDA 2.2", evidence_keys=["ppda_8m"]) for lang in langs])
        return writes_good(m, persona, langs, fb)
    cues, info = await run(Storyteller(FakeWriter(persona_fn), roster=lambda _: set(), segments=SEGS), moment())
    assert info["source"] == "agent" and len(calls) == 4
    assert all(any("2.2" in p for p in fb) for fb in calls[2:])  # feedback reached each persona
    assert trace(info) == [
        ("editor", None, 0, "aired"), ("explainer", None, 0, "ok"),
        ("personalizer", "analyst", 0, "ok"), ("personalizer", "casual", 0, "ok"),
        ("verifier", "analyst", 0, "rejected"), ("verifier", "casual", 0, "rejected"),
        ("personalizer", "analyst", 1, "ok"), ("personalizer", "casual", 1, "ok"),
        ("verifier", "analyst", 1, "accepted"), ("verifier", "casual", 1, "accepted"),
        ("director", None, 0, "ok")]
    steps = info["steps"]
    assert "2.2" in steps[4]["detail"] and steps[2]["detail"] == "fake-model"
    assert all(0 <= a["t_ms"] <= b["t_ms"] for a, b in zip(steps, steps[1:], strict=False))
    assert info["started_at"] > 1e9


async def test_one_bad_persona_only_retries_and_falls_back_alone():
    writer = FakeWriter(lambda m, persona, langs, fb: (
        Explanation(variants=[Variant(persona=persona, lang=lang, title="x", text="PPDA 9.9", evidence_keys=["ppda_8m"]) for lang in langs])
        if persona == "casual" else writes_good(m, persona, langs, fb)))
    cues, info = await run(Storyteller(writer, roster=lambda _: set(), segments=SEGS), moment())
    assert [p for p, *_ in writer.persona_calls].count("analyst") == 1  # accepted first time, never redone
    assert [p for p, *_ in writer.persona_calls].count("casual") == 2   # one retry, then its template
    assert info["source"] == "partial" and info["personas"] == {"analyst": "agent", "casual": "fallback"}
    assert [t for t in trace(info) if t[0] == "verifier"] == [
        ("verifier", "analyst", 0, "accepted"), ("verifier", "casual", 0, "rejected"),
        ("verifier", "casual", 1, "fallback")]
    by = {c.persona: c for c in cues}
    assert by["analyst"].data["model"] == "fake-model" and by["casual"].data["fallbackReason"].startswith("verifier")


async def test_ungrounded_or_failed_angle_is_dropped_not_fatal():
    bad_angle = FakeWriter(writes_good, core_fn=lambda m: Core(angle="PPDA 1.1, best ever", evidence_keys=["ppda_8m"]))
    cues, info = await run(Storyteller(bad_angle, roster=lambda _: set(), segments=SEGS), moment())
    assert info["source"] == "agent" and all(core is None for _, _, core, _ in bad_angle.persona_calls)

    def boom(m):
        raise TimeoutError("slow")
    down = FakeWriter(writes_good, core_fn=boom)
    cues, info = await run(Storyteller(down, roster=lambda _: set(), segments=SEGS), moment())
    assert info["source"] == "agent" and len(cues) == 2


async def test_template_writer_needs_no_model():
    cues, info = await run(Storyteller(TemplateWriter(), roster=lambda _: set()), moment())
    assert len(cues) == len(DEFAULT_SEGMENTS) and all(c.verified for c in cues)
    assert info["source"] == "fallback"  # labelled as templates in the player, never as AI


async def test_model_down_falls_back_to_templates():
    def down(m, persona, langs, fb):
        raise TimeoutError("model timeout")
    cues, info = await run(Storyteller(FakeWriter(down), roster=lambda _: set(), segments=DEFAULT_SEGMENTS), moment())
    assert info["source"] == "fallback" and len(cues) == len(DEFAULT_SEGMENTS)
    # templates must pass the same verifier
    m = moment()
    from matchlens.story.templates import fallback_explanation
    assert verify(fallback_explanation(m, DEFAULT_SEGMENTS), m, DEFAULT_SEGMENTS, set()).ok


@pytest.mark.parametrize("kind", ["goal", "big_chance", "momentum_swing", "pressing_surge", "half_time", "full_time"])
@pytest.mark.parametrize("started", ["turnover", "build-up"])
@pytest.mark.parametrize("with_player", [True, False])
def test_templates_verify_for_every_kind(kind, started, with_player):
    from matchlens.story.templates import fallback_explanation
    m = moment(kind)
    m.evidence = [EvidenceItem(key=k, label=f"{k}, last {8 if k.startswith('ppda') else 10} min", value=v) for k, v in {
        "xg": 0.31, "chain_passes": 1 if started == "turnover" else 5, "chain_duration_s": 14, "started_from": started,
        "shot_speed_kmh": 96.4, "threat_10m": 0.9, "opp_threat_10m": 0.2, "ppda_8m": 4.5, "ppda_match": 11.7,
        "score": "1-0", "xg_home": 1.2, "xg_away": 0.4, "shots_home": 9, "shots_away": 4, "top_speed_kmh": 33.8}.items()]
    if with_player:
        m.player_ids, m.names = ["a7"], {**m.names, "a7": "Zeki Zelenko"}
        m.headline = "Zeki Zelenko scores for Ironvale Athletic"
    expl = fallback_explanation(m, DEFAULT_SEGMENTS)
    assert verify(expl, m, DEFAULT_SEGMENTS, {"Zeki Zelenko", "Other Player"}).ok
    scout = next(v for v in expl.variants if v.persona == "scout" and v.lang == "en")
    if with_player and kind in ("goal", "big_chance", "half_time", "full_time"):
        assert "Zeki Zelenko" in scout.text  # the scout note is about the player
    texts = {v.persona: v.text for v in expl.variants if v.lang == "en"}
    assert len(set(texts.values())) == len(PERSONAS)  # four genuinely different registers


async def test_overlay_budget_skips_minor_moments():
    story = Storyteller(FakeWriter(writes_good), roster=lambda _: set(), segments=SEGS)
    _, first = await run(story, moment(clock=3000))
    _, second = await run(story, moment(clock=3020))
    assert first["cues"] == 2 and second["skipped"] == "overlay budget"
    assert trace(second) == [("editor", None, 0, "skipped")] and second["steps"][0]["detail"] == "overlay budget"


async def test_crash_mid_workflow_resumes_from_checkpoint():
    storage = InMemoryCheckpointStorage()
    writer = FakeWriter(writes_good)
    crashed = {"n": 0}
    def fault(_):
        if crashed["n"] == 0:
            crashed["n"] += 1
            raise RuntimeError("replica killed")
    story = Storyteller(writer, roster=lambda _: set(), checkpoint_storage=storage, segments=SEGS, fault=fault)
    with pytest.raises(Exception):  # noqa: B017 - the workflow wraps the injected fault
        await run(story, moment())
    # A fresh replica picks up the redelivered message and resumes
    story2 = Storyteller(writer, roster=lambda _: set(), checkpoint_storage=storage, segments=SEGS, fault=fault)
    cues, info = await run(story2, moment())
    assert info["resumed_from"] is not None
    assert len(cues) == 2
    # the models were NOT called again: work resumed after the Personalizers
    assert len(writer.core_calls) == 1 and len(writer.persona_calls) == 2
    # the killed replica's steps came back with the checkpoint, and the resumed Director follows them
    steps = trace(info)
    assert steps[0] == ("editor", None, 0, "aired") and steps[-1] == ("director", None, 0, "ok")
    assert [s for s in steps if s[0] == "personalizer"] and [s for s in steps if s[0] == "verifier"]
    assert all(a["t_ms"] + a["ms"] <= info["steps"][-1]["t_ms"] for a in info["steps"][:-1])
