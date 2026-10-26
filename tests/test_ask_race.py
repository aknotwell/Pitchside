import asyncio
import itertools

import httpx
import pytest

from matchlens.reflex.ask import Decision, ReflexResult
from matchlens.services import api

ips = itertools.count(1)


class StubReflex:
    deployment = "gpt-4.1-mini"

    def __init__(self, text=None, delay=0.0):
        self.text, self.delay, self.cancelled = text, delay, False

    async def answer(self, question, lang, read_state):
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.text:
            return ReflexResult(Decision("answer", "score", 1.0, ""), self.text, 0)
        return ReflexResult(Decision("pass", None, 1.0, "no_capability"))


class StubAgent:
    def __init__(self, delay=0.0, error=None):
        self.delay, self.error, self.calls, self.cancelled = delay, error, 0, False

    async def ask(self, match_id, question, lang):
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error:
            raise self.error
        return {"answer": "from the agent", "tools": ["get_match_state"], "model": "gpt-4.1-mini"}


@pytest.fixture(autouse=True)
def short_head_start(monkeypatch):
    monkeypatch.setattr(api, "AGENT_HEAD_START_S", 0.1)


async def ask(reflex, agent):
    api.app.state.reflex, api.app.state.ask = reflex, agent
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://t") as c:
        return await c.post("/api/ask", json={"matchId": "m1", "question": "score?"},
                            headers={"x-forwarded-for": f"10.9.0.{next(ips)}"})


async def test_a_fast_reflex_answer_never_starts_the_agent():
    agent = StubAgent()
    r = await ask(StubReflex("1-0"), agent)
    assert r.json()["answer"] == "1-0" and r.json()["model"] == "reflex"
    assert agent.calls == 0


async def test_a_reflex_pass_goes_to_the_agent_with_telemetry():
    r = await ask(StubReflex(), StubAgent())
    assert r.json()["answer"] == "from the agent"
    assert r.json()["reflex"]["reason"] == "no_capability"


async def test_a_slow_reflex_answer_cancels_the_running_agent():
    agent = StubAgent(delay=5)
    r = await ask(StubReflex("1-0", delay=0.3), agent)
    assert r.json()["answer"] == "1-0"
    assert agent.calls == 1 and agent.cancelled


async def test_an_agent_that_finishes_first_wins_and_cancels_reflex():
    reflex = StubReflex("1-0", delay=5)
    r = await ask(reflex, StubAgent(delay=0.1))
    assert r.json()["answer"] == "from the agent" and r.json()["reflex"]["reason"] == "agent_first"
    assert reflex.cancelled


@pytest.mark.parametrize(("error", "status"), [(TimeoutError(), 504), (RuntimeError("mcp down"), 502)])
async def test_agent_failures_after_a_pass_map_to_http_errors(error, status):
    r = await ask(StubReflex(), StubAgent(error=error))
    assert r.status_code == status
