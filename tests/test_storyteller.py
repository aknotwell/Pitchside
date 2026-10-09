import pytest
from agent_framework import InMemoryCheckpointStorage

from matchlens.contracts import EvidenceItem, Moment
from matchlens.story.verifier import Explanation, Variant, verify
from matchlens.story.workflow import DEFAULT_SEGMENTS, Storyteller

SEGS = [("analyst", "en"), ("casual", "es")]


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


async def run(story, m):
    return await story.tell(m)


async def test_agent_path_produces_verified_cues():
    async def explain(m, segs, fb):
        return good(m, segs), "fake-model"
    story = Storyteller(explain, roster=lambda _: set(), segments=SEGS)
    cues, info = await run(story, moment())
    assert info["source"] == "agent" and len(cues) == 2
    assert {c.lang for c in cues} == {"en", "es"}
    assert all(c.verified and c.evidence.metrics for c in cues)


async def test_retry_then_success():
    calls = []
    async def explain(m, segs, fb):
        calls.append(fb)
        if len(calls) == 1:
            return Explanation(variants=[Variant(persona=p, lang=lang, title="x", text="PPDA 2.2", evidence_keys=["ppda_8m"]) for p, lang in segs]), "fake"
        return good(m, segs), "fake"
    cues, info = await run(Storyteller(explain, roster=lambda _: set(), segments=SEGS), moment())
    assert info["source"] == "agent" and len(calls) == 2
    assert any("2.2" in p for p in calls[1])  # feedback reached the explainer


async def test_model_down_falls_back_to_templates():
    async def explain(m, segs, fb):
        raise TimeoutError("model timeout")
    cues, info = await run(Storyteller(explain, roster=lambda _: set(), segments=DEFAULT_SEGMENTS), moment())
    assert info["source"] == "fallback" and len(cues) == len(DEFAULT_SEGMENTS)
    # templates must pass the same verifier
    m = moment()
    from matchlens.story.templates import fallback_explanation
    assert verify(fallback_explanation(m, DEFAULT_SEGMENTS), m, DEFAULT_SEGMENTS, set()).ok


@pytest.mark.parametrize("kind", ["goal", "big_chance", "momentum_swing", "half_time"])
def test_templates_verify_for_every_kind(kind):
    from matchlens.story.templates import fallback_explanation
    m = moment(kind)
    m.evidence = [EvidenceItem(key=k, label=f"{k}, last 10 min", value=v) for k, v in {
        "xg": 0.31, "chain_passes": 5, "chain_duration_s": 14, "threat_10m": 0.9, "opp_threat_10m": 0.2,
        "score": "1-0", "xg_home": 1.2, "xg_away": 0.4}.items()]
    assert verify(fallback_explanation(m, DEFAULT_SEGMENTS), m, DEFAULT_SEGMENTS, set()).ok


async def test_overlay_budget_skips_minor_moments():
    async def explain(m, segs, fb):
        return good(m, segs), "fake"
    story = Storyteller(explain, roster=lambda _: set(), segments=SEGS)
    _, first = await run(story, moment(clock=3000))
    _, second = await run(story, moment(clock=3020))
    assert first["cues"] == 2 and second["skipped"] == "overlay budget"


async def test_crash_mid_workflow_resumes_from_checkpoint():
    storage = InMemoryCheckpointStorage()
    calls = []
    async def explain(m, segs, fb):
        calls.append(1)
        return good(m, segs), "fake"
    crashed = {"n": 0}
    def fault(_):
        if crashed["n"] == 0:
            crashed["n"] += 1
            raise RuntimeError("replica killed")
    story = Storyteller(explain, roster=lambda _: set(), checkpoint_storage=storage, segments=SEGS, fault=fault)
    with pytest.raises(Exception):  # noqa: B017 - the workflow wraps the injected fault
        await run(story, moment())
    # A fresh replica picks up the redelivered message and resumes
    story2 = Storyteller(explain, roster=lambda _: set(), checkpoint_storage=storage, segments=SEGS, fault=fault)
    cues, info = await run(story2, moment())
    assert info["resumed_from"] is not None
    assert len(cues) == 2
    assert len(calls) == 1  # the model was NOT called again: work resumed after the explainer
