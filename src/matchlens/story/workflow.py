"""The storyteller: a Microsoft Agent Framework workflow, one run per moment.

    Editor ──► Explainer ──► Verifier ──ok──────────────► Director ──► cues
                  ▲              │
                  └── retry (1) ─┤
                                 └─ fail twice / model down ─► Director (template fallback)

* Editor (code): enforces the overlay budget and decides what airs.
* Explainer (LLM via Foundry): writes every persona x language variant in ONE
  structured call, from the evidence pack only.
* Verifier (code): every number, name and evidence key must trace to the pack.
* Director (code): picks the overlay template, timing, priority and TTL.

Each run checkpoints at every superstep, so a replica killed mid-moment
resumes from the last checkpoint on another replica.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Never, Protocol

from agent_framework import (
    CheckpointStorage,
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)

from matchlens.contracts import Moment, OverlayCue
from matchlens.story.templates import fallback_explanation
from matchlens.story.verifier import Explanation, verify

log = logging.getLogger("matchlens.storyteller")

DEFAULT_SEGMENTS: list[tuple[str, str]] = [
    ("analyst", "en"), ("casual", "en"),
    ("analyst", "es"), ("casual", "es"),
    ("analyst", "ja"), ("casual", "ja"),
]
ALWAYS_AIR = {"goal", "half_time", "full_time"}
TEMPLATES = {
    "goal": ("lower_third.goal_story", 1, 12.0),
    "big_chance": ("lower_third.big_chance", 2, 9.0),
    "momentum_swing": ("graphic.momentum_swing", 2, 10.0),
    "pressing_surge": ("graphic.pressing", 2, 10.0),
    "half_time": ("panel.recap", 1, 20.0),
    "full_time": ("panel.recap", 1, 30.0),
}
DISPLAY_OFFSET_S = 3.0  # explanations air ~3 s after the play, inside the broadcast delay

INSTRUCTIONS = """You are the explanation writer for a live football broadcast overlay.
You receive one match MOMENT as JSON: what happened plus an EVIDENCE pack of
pre-computed, verified metrics. Write why the moment matters.

Hard rules (a code verifier rejects violations):
- Use ONLY numbers that appear in the evidence pack, the score, or the minute. Never invent or
  compute new numbers. You may round to fewer decimals or write 0.83 as 83%.
- Only name the people and clubs listed in "names". Never mention betting or odds.
- List the evidence keys each variant relies on in evidence_keys.
- Produce exactly one variant for every requested persona/lang pair.
- analyst: precise, tactical, cites 2-3 metrics. casual: warm, plain words, at most 1 number.
- Write natively in the requested language (en, es, ja). Club and player names stay EXACTLY as given,
  in Latin script, even in Japanese (never transliterate them).
- title <= 70 characters; text <= 260 characters, one or two sentences.
"""


# ----------------------------------------------------------------- messages
@dataclass
class Draft:
    moment: Moment
    attempt: int = 0
    feedback: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    moment: Moment
    attempt: int
    explanation: Explanation | None
    error: str | None = None
    model: str | None = None
    latency_ms: int = 0


@dataclass
class Approved:
    moment: Moment
    explanation: Explanation
    source: str  # agent | fallback
    reason: str = ""
    model: str | None = None


@dataclass
class Skipped:
    moment_id: str
    reason: str


CHECKPOINT_TYPES = [f"matchlens.story.workflow:{n}" for n in ("Draft", "Candidate", "Approved", "Skipped")] + [
    "matchlens.contracts:Moment",
    "matchlens.contracts:EvidenceItem",
    "matchlens.story.verifier:Explanation",
    "matchlens.story.verifier:Variant",
]


class ExplainFn(Protocol):
    def __call__(self, moment: Moment, segments: list[tuple[str, str]], feedback: list[str]) -> Awaitable[tuple[Explanation, str]]: ...


# ----------------------------------------------------------------- executors
class Editor(Executor):
    """Decides what airs: always goals and recaps; otherwise at most one explanation per budget window."""

    def __init__(self, budget: OverlayBudget):
        super().__init__(id="editor")
        self.budget = budget

    @handler
    async def run(self, moment: Moment, ctx: WorkflowContext[Draft, Skipped]) -> None:
        if moment.kind in ALWAYS_AIR or self.budget.allow(moment.match_id, moment.clock_s):
            await ctx.send_message(Draft(moment=moment))
        else:
            await ctx.yield_output(Skipped(moment.moment_id, "overlay budget"))


class Explainer(Executor):
    def __init__(self, explain: ExplainFn, segments: list[tuple[str, str]]):
        super().__init__(id="explainer")
        self.explain = explain
        self.segments = segments

    @handler
    async def run(self, draft: Draft, ctx: WorkflowContext[Candidate]) -> None:
        t0 = time.perf_counter()
        try:
            expl, model = await self.explain(draft.moment, self.segments, draft.feedback)
            await ctx.send_message(Candidate(draft.moment, draft.attempt, expl, model=model,
                                             latency_ms=int(1000 * (time.perf_counter() - t0))))
        except Exception as exc:  # model down, timeout, bad JSON
            log.warning("explainer failed for %s: %s", draft.moment.moment_id, exc)
            await ctx.send_message(Candidate(draft.moment, draft.attempt, None, error=f"{type(exc).__name__}: {exc}"[:300]))


class Verifier(Executor):
    def __init__(self, segments: list[tuple[str, str]], roster: Callable[[str], set[str]], max_retries: int = 1):
        super().__init__(id="verifier")
        self.segments = segments
        self.roster = roster
        self.max_retries = max_retries

    @handler
    async def run(self, cand: Candidate, ctx: WorkflowContext[Draft | Approved]) -> None:
        m = cand.moment
        if cand.explanation is None:
            await ctx.send_message(Approved(m, fallback_explanation(m, self.segments), "fallback", cand.error or "model error"))
            return
        result = verify(cand.explanation, m, self.segments, self.roster(m.match_id))
        if result.ok:
            await ctx.send_message(Approved(m, cand.explanation, "agent", model=cand.model))
        elif cand.attempt < self.max_retries:
            log.info("verifier rejected %s (attempt %d): %s", m.moment_id, cand.attempt, result.problems[:5])
            await ctx.send_message(Draft(m, cand.attempt + 1, result.problems[:12]))
        else:
            await ctx.send_message(Approved(m, fallback_explanation(m, self.segments), "fallback",
                                            "verifier: " + "; ".join(result.problems[:4])))


class Director(Executor):
    def __init__(self, fault: Callable[[Approved], None] | None = None):
        super().__init__(id="director")
        self.fault = fault  # injected failure for the chaos drill and tests

    @handler
    async def run(self, ok: Approved, ctx: WorkflowContext[Never, list[OverlayCue]]) -> None:
        if self.fault:
            self.fault(ok)
        m = ok.moment
        template, priority, ttl = TEMPLATES[m.kind]
        cues = []
        for v in ok.explanation.variants:
            used = [e for e in m.evidence if e.key in set(v.evidence_keys)] or m.evidence[:3]
            cues.append(OverlayCue(
                id=f"{m.moment_id}-{v.persona}-{v.lang}",
                matchId=m.match_id,
                display_at_s=round(m.clock_s + DISPLAY_OFFSET_S, 2),
                ttl_s=ttl,
                priority=priority,
                template=template,
                persona=v.persona,  # type: ignore[arg-type]
                lang=v.lang,
                title=v.title,
                text=v.text,
                data={"kind": m.kind, "team": m.team_id, "headline": m.headline, "score": m.score,
                      "model": ok.model, "fallbackReason": ok.reason or None, "enqueuedAtMs": m.enqueued_at_ms},
                evidence={"event_ids": m.event_ids, "metrics": [e.model_dump() for e in used]},
                verified=True,
                source="agent" if ok.source == "agent" else "fallback",
                moment_id=m.moment_id,
                trace_id=m.trace_id,
            ))
        await ctx.yield_output(cues)


# ----------------------------------------------------------------- budget
class OverlayBudget:
    """At most one non-essential explanation per ``window_s`` of match clock, per match."""

    def __init__(self, window_s: float = 75.0):
        self.window_s = window_s
        self.last: dict[str, float] = {}

    def allow(self, match_id: str, clock_s: float) -> bool:
        if clock_s - self.last.get(match_id, -1e9) >= self.window_s:
            self.last[match_id] = clock_s
            return True
        return False


# ----------------------------------------------------------------- runner
class Storyteller:
    def __init__(self, explain: ExplainFn, roster: Callable[[str], set[str]],
                 checkpoint_storage: CheckpointStorage | None = None,
                 segments: list[tuple[str, str]] | None = None, budget: OverlayBudget | None = None,
                 fault: Callable[[Approved], None] | None = None):
        self.explain = explain
        self.roster = roster
        self.storage = checkpoint_storage
        self.segments = segments or DEFAULT_SEGMENTS
        self.budget = budget or OverlayBudget()
        self.fault = fault

    def build(self, moment_id: str):
        editor = Editor(self.budget)
        explainer = Explainer(self.explain, self.segments)
        verifier = Verifier(self.segments, self.roster)
        director = Director(self.fault)
        return (
            WorkflowBuilder(name=f"story-{moment_id}", start_executor=editor, checkpoint_storage=self.storage)
            .add_edge(editor, explainer)
            .add_edge(explainer, verifier)
            .add_edge(verifier, explainer, condition=lambda msg: isinstance(msg, Draft))
            .add_edge(verifier, director, condition=lambda msg: isinstance(msg, Approved))
            .build()
        )

    async def tell(self, moment: Moment) -> tuple[list[OverlayCue], dict]:
        """Run (or resume) the workflow for one moment. Returns cues and run info."""
        wf = self.build(moment.moment_id)
        resumed_from = None
        if self.storage is not None:
            latest = await self.storage.get_latest(workflow_name=f"story-{moment.moment_id}")
            if latest is not None:
                resumed_from = latest.checkpoint_id
        t0 = time.perf_counter()
        if resumed_from:
            log.info("resuming %s from checkpoint %s", moment.moment_id, resumed_from)
            result = await wf.run(checkpoint_id=resumed_from)
        else:
            result = await wf.run(moment)
        cues: list[OverlayCue] = []
        skipped = None
        for out in result.get_outputs():
            if isinstance(out, list):
                cues.extend(out)
            elif isinstance(out, Skipped):
                skipped = out.reason
        info = {"moment_id": moment.moment_id, "kind": moment.kind, "resumed_from": resumed_from,
                "skipped": skipped, "cues": len(cues), "ms": int(1000 * (time.perf_counter() - t0)),
                "source": cues[0].source if cues else ("duplicate" if resumed_from else None)}
        return cues, info


# ----------------------------------------------------------------- Foundry explainer
def moment_prompt(moment: Moment, segments: list[tuple[str, str]], feedback: list[str]) -> str:
    payload = {
        "kind": moment.kind,
        "headline": moment.headline,
        "minute": int(moment.clock_s // 60),
        "team": moment.team_name,
        "score": moment.score,
        "names": moment.names,
        "evidence": [e.model_dump(exclude_none=True) for e in moment.evidence],
        "requested": [{"persona": p, "lang": lang} for p, lang in segments],
    }
    text = "MOMENT:\n" + json.dumps(payload, ensure_ascii=False)
    if feedback:
        text += "\n\nYour previous draft was REJECTED by the verifier. Fix exactly these problems:\n- " + "\n- ".join(feedback)
    return text


class FoundryExplainer:
    """Calls Foundry through Agent Framework with a deployment fallback chain and hard caps."""

    def __init__(self, project_endpoint: str, deployments: list[str], credential, timeout_s: float = 20.0,
                 max_output_tokens: int = 4000):
        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient

        self.deployments = deployments
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.agents = [
            Agent(FoundryChatClient(project_endpoint=project_endpoint, model=d, credential=credential),
                  instructions=INSTRUCTIONS, name=f"explainer-{d}")
            for d in deployments
        ]

    async def __call__(self, moment: Moment, segments: list[tuple[str, str]], feedback: list[str]) -> tuple[Explanation, str]:
        prompt = moment_prompt(moment, segments, feedback)
        last: Exception | None = None
        for dep, agent in zip(self.deployments, self.agents, strict=True):
            try:
                options = {"response_format": Explanation, "max_tokens": self.max_output_tokens}
                if dep.startswith(("gpt-5", "gpt-6", "o")):
                    options["reasoning"] = {"effort": "minimal" if dep.startswith("gpt-5") else "low"}
                timeout = self.timeout_s * (2.0 if moment.kind in ("half_time", "full_time") else 1.0)
                resp = await asyncio.wait_for(agent.run(prompt, options=options), timeout=timeout)
                value = resp.value
                if value is None:
                    raise ValueError("model returned no structured output")
                return value, dep
            except Exception as exc:  # try the next deployment
                last = exc
                log.warning("deployment %s failed: %s %s", dep, type(exc).__name__, exc)
        raise RuntimeError(f"all deployments failed: {last}")
