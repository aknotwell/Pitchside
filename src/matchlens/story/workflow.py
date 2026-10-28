"""The storyteller: a Microsoft Agent Framework workflow, one run per moment.

    Editor ──► Explainer ──► Personalizer ──► Verifier ──all settled──► Director ──► cues
                                  ▲               │
                                  └─ retry the ───┤
                                     rejected     └─ rejected twice / model down ─► template for that persona
                                     personas (1)

* Editor (code): enforces the overlay budget and decides what airs.
* Explainer (LLM via Foundry): writes the crew's shared angle on the moment in one short
  call, from the evidence pack only. If it is slow or ungrounded, the Personalizers go on
  without it.
* Personalizer (LLM via Foundry): one agent per persona (analyst, fan, academy coach, scout),
  run concurrently. Each rewrites the angle in its own register for every language.
* Verifier (code): every number, name and evidence key must trace to the pack. Personas are
  judged separately, so one bad draft only sends that persona back (or to its template).
* Director (code): picks the overlay template, timing, priority and TTL.

Each run checkpoints at every superstep, so a replica killed mid-moment
resumes from the last checkpoint on another replica.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Never, Protocol

from agent_framework import (
    CheckpointStorage,
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)

from matchlens.contracts import LANGUAGES, PERSONAS, Moment, OverlayCue
from matchlens.story.personas import STYLES
from matchlens.story.templates import fallback_explanation
from matchlens.story.verifier import Core, Explanation, Variant, verify, verify_core

log = logging.getLogger("matchlens.storyteller")

DEFAULT_SEGMENTS: list[tuple[str, str]] = [(p, lang) for lang in LANGUAGES for p in PERSONAS]
ALWAYS_AIR = {"goal", "half_time", "full_time"}
TEMPLATES = {
    "goal": ("lower_third.goal_story", 1, 12.0),
    "big_chance": ("lower_third.big_chance", 2, 9.0),
    "momentum_swing": ("graphic.momentum_swing", 2, 10.0),
    "pressing_surge": ("graphic.pressing", 2, 10.0),
    "half_time": ("panel.recap", 1, 20.0),
    "full_time": ("panel.recap", 1, 30.0),
}
TEMPLATE_MODEL = "templates"  # what TemplateWriter reports; its cues are labelled as templates, not AI
DISPLAY_OFFSET_S = 3.0  # explanations air ~3 s after the play, inside the broadcast delay

RULES = """Hard rules (a code verifier rejects violations):
- Use ONLY numbers that appear in the evidence pack, the score, or the minute. Never invent or
  compute new numbers. You may round to fewer decimals or write 0.83 as 83%.
- Only name the people and clubs listed in "names". Never mention betting or odds.
- List the evidence keys you rely on in evidence_keys.
"""

CORE_INSTRUCTIONS = f"""You lead the explanation crew of a live football broadcast.
You receive one match MOMENT as JSON: what happened plus an EVIDENCE pack of
pre-computed, verified metrics. In English, write the ANGLE: the single reason this
moment matters, in one or two sentences of at most 200 characters. Four colleagues
will rewrite it for an analyst, a casual fan, an academy coach and a scout.

{RULES}"""


def persona_instructions(persona: str) -> str:
    return f"""You write live football broadcast overlays for one kind of viewer.
{STYLES[persona]}

You receive one match MOMENT as JSON: what happened, an EVIDENCE pack of pre-computed,
verified metrics, and usually the crew's shared ANGLE. Stay consistent with the angle,
but write for your viewer.

{RULES}- Produce exactly one variant per requested language, with persona "{persona}".
- Write natively in the requested language (en, es, ja). Club and player names stay EXACTLY as given,
  in Latin script, even in Japanese (never transliterate them).
- title <= 70 characters; text <= 260 characters, one or two sentences.
"""


# ----------------------------------------------------------------- messages
@dataclass
class Draft:
    moment: Moment


@dataclass
class Brief:
    """What the Personalizers write from. Also carries the personas already settled, across retries."""

    moment: Moment
    core: Core | None
    pending: list[str]
    attempt: int = 0
    feedback: dict[str, list[str]] = field(default_factory=dict)
    accepted: list[Variant] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)  # persona -> agent | fallback
    models: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)


@dataclass
class Candidate:
    brief: Brief
    drafts: dict[str, Explanation]
    errors: dict[str, str]
    models: dict[str, str]
    latency_ms: int = 0


@dataclass
class Approved:
    moment: Moment
    explanation: Explanation
    sources: dict[str, str]
    models: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)


@dataclass
class Skipped:
    moment_id: str
    reason: str


CHECKPOINT_TYPES = [f"matchlens.story.workflow:{n}" for n in ("Draft", "Brief", "Candidate", "Approved", "Skipped")] + [
    "matchlens.contracts:Moment",
    "matchlens.contracts:EvidenceItem",
    "matchlens.story.verifier:Core",
    "matchlens.story.verifier:Explanation",
    "matchlens.story.verifier:Variant",
]


class Writer(Protocol):
    """The model side of the crew. ``FoundryExplainer`` on Azure, ``TemplateWriter`` locally."""

    async def core(self, moment: Moment) -> tuple[Core, str]: ...

    async def persona(self, moment: Moment, persona: str, langs: list[str], core: Core | None,
                      feedback: list[str]) -> tuple[Explanation, str]: ...


class StepLog:
    """What each agent did in one ``tell()``. Executors checkpoint it, so a resumed run keeps the steps the killed
    replica finished; their times are shifted so the resumed steps follow on."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.steps: list[dict] = []

    def restore(self, steps: list[dict]) -> None:
        if len(steps) > len(self.steps):
            self.steps = [dict(s) for s in steps]
            self.t0 = time.perf_counter() - max(s["t_ms"] + s["ms"] for s in steps) / 1000

    def add(self, step: str, outcome: str, start: float, *, persona: str | None = None, attempt: int = 0,
            detail: str | None = None) -> None:
        now = time.perf_counter()
        self.steps.append({"step": step, "persona": persona, "attempt": attempt,
                           "t_ms": int(1000 * (start - self.t0)), "ms": int(1000 * (now - start)),
                           "outcome": outcome, "detail": detail[:200] if detail else None})


class Traced(Executor):
    """An executor that writes to the run's StepLog and carries it through checkpoints."""

    steps: StepLog

    async def on_checkpoint_save(self) -> dict:
        return {"steps": self.steps.steps}

    async def on_checkpoint_restore(self, state: dict) -> None:
        self.steps.restore(state.get("steps", []))


def _langs(segments: list[tuple[str, str]], persona: str) -> list[str]:
    return [lang for p, lang in segments if p == persona]


def _personas(segments: list[tuple[str, str]]) -> list[str]:
    return list(dict.fromkeys(p for p, _ in segments))


# ----------------------------------------------------------------- executors
class Editor(Traced):
    """Decides what airs: always goals and recaps; otherwise at most one explanation per budget window."""

    def __init__(self, budget: OverlayBudget, steps: StepLog):
        super().__init__(id="editor")
        self.budget = budget
        self.steps = steps

    @handler
    async def run(self, moment: Moment, ctx: WorkflowContext[Draft, Skipped]) -> None:
        start = time.perf_counter()
        if moment.kind in ALWAYS_AIR or self.budget.allow(moment.match_id, moment.clock_s):
            self.steps.add("editor", "aired", start)
            await ctx.send_message(Draft(moment=moment))
        else:
            self.steps.add("editor", "skipped", start, detail="overlay budget")
            await ctx.yield_output(Skipped(moment.moment_id, "overlay budget"))


class Explainer(Traced):
    """Writes the shared angle. Optional: a failed or ungrounded angle is dropped, never retried."""

    def __init__(self, writer: Writer, segments: list[tuple[str, str]], roster: Callable[[str], set[str]],
                 steps: StepLog, timeout_s: float = 8.0):
        super().__init__(id="explainer")
        self.writer = writer
        self.segments = segments
        self.roster = roster
        self.steps = steps
        self.timeout_s = timeout_s

    @handler
    async def run(self, draft: Draft, ctx: WorkflowContext[Brief]) -> None:
        m = draft.moment
        core = None
        start = time.perf_counter()
        try:
            recap = m.kind in ("half_time", "full_time")
            core, model = await asyncio.wait_for(self.writer.core(m), timeout=self.timeout_s * (2.0 if recap else 1.0))
            result = verify_core(core, m, self.roster(m.match_id))
            if result.ok:
                self.steps.add("explainer", "ok", start, detail=model)
            else:
                log.info("angle for %s dropped: %s", m.moment_id, result.problems[:3])
                self.steps.add("explainer", "dropped", start, detail="; ".join(result.problems[:3]))
                core = None
        except Exception as exc:  # model down, timeout, bad JSON
            log.warning("explainer failed for %s: %s", m.moment_id, exc)
            self.steps.add("explainer", "failed", start, detail=f"{type(exc).__name__}: {exc}")
        await ctx.send_message(Brief(m, core, pending=_personas(self.segments)))


class Personalizer(Traced):
    """One concurrent model call per pending persona, each covering all of that persona's languages."""

    def __init__(self, writer: Writer, segments: list[tuple[str, str]], steps: StepLog):
        super().__init__(id="personalizer")
        self.writer = writer
        self.segments = segments
        self.steps = steps

    @handler
    async def run(self, brief: Brief, ctx: WorkflowContext[Candidate]) -> None:
        t0 = time.perf_counter()
        m = brief.moment

        async def one(persona: str):
            start = time.perf_counter()
            try:
                expl, model = await self.writer.persona(m, persona, _langs(self.segments, persona), brief.core,
                                                        brief.feedback.get(persona, []))
                variants = [v.model_copy(update={"persona": persona}) for v in expl.variants]
                self.steps.add("personalizer", "ok", start, persona=persona, attempt=brief.attempt, detail=model)
                return persona, Explanation(variants=variants), model, None
            except Exception as exc:
                log.warning("personalizer %s failed for %s: %s", persona, m.moment_id, exc)
                err = f"{type(exc).__name__}: {exc}"[:300]
                self.steps.add("personalizer", "failed", start, persona=persona, attempt=brief.attempt, detail=err)
                return persona, None, None, err

        drafts, errors, models = {}, {}, {}
        for persona, expl, model, err in await asyncio.gather(*(one(p) for p in brief.pending)):
            if expl is not None:
                drafts[persona], models[persona] = expl, model
            else:
                errors[persona] = err
        await ctx.send_message(Candidate(brief, drafts, errors, models, int(1000 * (time.perf_counter() - t0))))


class Verifier(Traced):
    def __init__(self, segments: list[tuple[str, str]], roster: Callable[[str], set[str]], steps: StepLog,
                 max_retries: int = 1):
        super().__init__(id="verifier")
        self.segments = segments
        self.roster = roster
        self.steps = steps
        self.max_retries = max_retries

    @handler
    async def run(self, cand: Candidate, ctx: WorkflowContext[Brief | Approved]) -> None:
        b = cand.brief
        m = b.moment
        accepted, sources = list(b.accepted), dict(b.sources)
        models, reasons = dict(b.models), dict(b.reasons)
        retry: dict[str, list[str]] = {}

        def fall_back(persona: str, reason: str, start: float) -> None:
            segs = [(persona, lang) for lang in _langs(self.segments, persona)]
            accepted.extend(fallback_explanation(m, segs).variants)
            sources[persona], reasons[persona] = "fallback", reason
            self.steps.add("verifier", "fallback", start, persona=persona, attempt=b.attempt, detail=reason)

        for persona in b.pending:
            start = time.perf_counter()
            if persona in cand.errors:
                fall_back(persona, cand.errors[persona] or "model error", start)
                continue
            segs = [(persona, lang) for lang in _langs(self.segments, persona)]
            result = verify(cand.drafts[persona], m, segs, self.roster(m.match_id))
            if result.ok and cand.models[persona] == TEMPLATE_MODEL:
                accepted.extend(cand.drafts[persona].variants)
                sources[persona], reasons[persona] = "fallback", "no model configured"
                self.steps.add("verifier", "fallback", start, persona=persona, attempt=b.attempt,
                             detail="no model configured")
            elif result.ok:
                accepted.extend(cand.drafts[persona].variants)
                sources[persona], models[persona] = "agent", cand.models[persona]
                self.steps.add("verifier", "accepted", start, persona=persona, attempt=b.attempt)
            elif b.attempt < self.max_retries:
                log.info("verifier rejected %s/%s (attempt %d): %s", m.moment_id, persona, b.attempt, result.problems[:5])
                retry[persona] = result.problems[:12]
                self.steps.add("verifier", "rejected", start, persona=persona, attempt=b.attempt,
                             detail="; ".join(result.problems[:3]))
            else:
                fall_back(persona, "verifier: " + "; ".join(result.problems[:4]), start)

        if retry:
            await ctx.send_message(Brief(m, b.core, pending=list(retry), attempt=b.attempt + 1, feedback=retry,
                                         accepted=accepted, sources=sources, models=models, reasons=reasons))
        else:
            await ctx.send_message(Approved(m, Explanation(variants=accepted), sources, models, reasons))


class Director(Traced):
    def __init__(self, steps: StepLog, fault: Callable[[Approved], None] | None = None):
        super().__init__(id="director")
        self.steps = steps
        self.fault = fault  # injected failure for the chaos drill and tests

    @handler
    async def run(self, ok: Approved, ctx: WorkflowContext[Never, list[OverlayCue]]) -> None:
        start = time.perf_counter()
        if self.fault:
            self.fault(ok)
        m = ok.moment
        template, priority, ttl = TEMPLATES[m.kind]
        cues = []
        for v in ok.explanation.variants:
            used = [e for e in m.evidence if e.key in set(v.evidence_keys)] or m.evidence[:3]
            agent = ok.sources.get(v.persona) == "agent"
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
                      "model": ok.models.get(v.persona) if agent else None,
                      "fallbackReason": None if agent else ok.reasons.get(v.persona),
                      "enqueuedAtMs": m.enqueued_at_ms},
                evidence={"event_ids": m.event_ids, "metrics": [e.model_dump() for e in used]},
                verified=True,
                source="agent" if agent else "fallback",
                moment_id=m.moment_id,
                trace_id=m.trace_id,
            ))
        self.steps.add("director", "ok", start)
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
    def __init__(self, writer: Writer, roster: Callable[[str], set[str]],
                 checkpoint_storage: CheckpointStorage | None = None,
                 segments: list[tuple[str, str]] | None = None, budget: OverlayBudget | None = None,
                 fault: Callable[[Approved], None] | None = None):
        self.writer = writer
        self.roster = roster
        self.storage = checkpoint_storage
        self.segments = segments or DEFAULT_SEGMENTS
        self.budget = budget or OverlayBudget()
        self.fault = fault

    def build(self, moment_id: str, steps: StepLog):
        editor = Editor(self.budget, steps)
        explainer = Explainer(self.writer, self.segments, self.roster, steps)
        personalizer = Personalizer(self.writer, self.segments, steps)
        verifier = Verifier(self.segments, self.roster, steps)
        director = Director(steps, self.fault)
        return (
            WorkflowBuilder(name=f"story-{moment_id}", start_executor=editor, checkpoint_storage=self.storage)
            .add_edge(editor, explainer)
            .add_edge(explainer, personalizer)
            .add_edge(personalizer, verifier)
            .add_edge(verifier, personalizer, condition=lambda msg: isinstance(msg, Brief))
            .add_edge(verifier, director, condition=lambda msg: isinstance(msg, Approved))
            .build()
        )

    async def tell(self, moment: Moment) -> tuple[list[OverlayCue], dict]:
        """Run (or resume) the workflow for one moment. Returns cues and run info."""
        resumed_from = None
        if self.storage is not None:
            latest = await self.storage.get_latest(workflow_name=f"story-{moment.moment_id}")
            if latest is not None:
                resumed_from = latest.checkpoint_id
        started_at = time.time()
        steps = StepLog()
        wf = self.build(moment.moment_id, steps)
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
        by_persona = {c.persona: c.source for c in cues}
        sources = set(by_persona.values())
        source = (sources.pop() if len(sources) == 1 else "partial") if cues else ("duplicate" if resumed_from else None)
        info = {"moment_id": moment.moment_id, "kind": moment.kind, "resumed_from": resumed_from,
                "skipped": skipped, "cues": len(cues), "ms": int(1000 * (time.perf_counter() - steps.t0)),
                "source": source, "personas": by_persona, "started_at": started_at, "steps": steps.steps}
        return cues, info


# ----------------------------------------------------------------- writers
def moment_payload(moment: Moment) -> dict:
    return {
        "kind": moment.kind,
        "headline": moment.headline,
        "minute": int(moment.clock_s // 60),
        "team": moment.team_name,
        "score": moment.score,
        "names": moment.names,
        "evidence": [e.model_dump(exclude_none=True) for e in moment.evidence],
    }


def core_prompt(moment: Moment) -> str:
    return "MOMENT:\n" + json.dumps(moment_payload(moment), ensure_ascii=False)


def persona_prompt(moment: Moment, persona: str, langs: list[str], core: Core | None, feedback: list[str]) -> str:
    payload = moment_payload(moment)
    if core is not None:
        payload["angle"] = core.angle
    payload["requested"] = [{"persona": persona, "lang": lang} for lang in langs]
    text = "MOMENT:\n" + json.dumps(payload, ensure_ascii=False)
    if feedback:
        text += "\n\nYour previous draft was REJECTED by the verifier. Fix exactly these problems:\n- " + "\n- ".join(feedback)
    return text


class TemplateWriter:
    """Local stand-in for Foundry: no model, no credentials. Writes the verified templates."""

    async def core(self, moment: Moment) -> tuple[Core, str]:
        return Core(angle=moment.headline, evidence_keys=[e.key for e in moment.evidence[:2]]), TEMPLATE_MODEL

    async def persona(self, moment: Moment, persona: str, langs: list[str], core: Core | None,
                      feedback: list[str]) -> tuple[Explanation, str]:
        return fallback_explanation(moment, [(persona, lang) for lang in langs]), TEMPLATE_MODEL


class FoundryExplainer:
    """The crew's agents on Foundry, via Agent Framework, with a deployment fallback chain and hard caps.

    One angle agent plus one Personalizer agent per persona, for each deployment.
    """

    def __init__(self, project_endpoint: str, deployments: list[str], credential, timeout_s: float = 20.0,
                 max_output_tokens: int = 4000):
        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient

        self.deployments = deployments
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        clients = {d: FoundryChatClient(project_endpoint=project_endpoint, model=d, credential=credential)
                   for d in deployments}
        self.agents = {
            role: [Agent(clients[d], instructions=instructions, name=f"{role}-{d}") for d in deployments]
            for role, instructions in [("explainer", CORE_INSTRUCTIONS),
                                       *((p, persona_instructions(p)) for p in PERSONAS)]
        }

    async def _run(self, role: str, prompt: str, response_format, timeout: float):
        last: Exception | None = None
        for dep, agent in zip(self.deployments, self.agents[role], strict=True):
            try:
                options = {"response_format": response_format, "max_tokens": self.max_output_tokens}
                if dep.startswith(("gpt-5", "gpt-6", "o")):
                    options["reasoning"] = {"effort": "minimal" if dep.startswith("gpt-5") else "low"}
                resp = await asyncio.wait_for(agent.run(prompt, options=options), timeout=timeout)
                value = resp.value
                if value is None:
                    raise ValueError("model returned no structured output")
                return value, dep
            except Exception as exc:  # try the next deployment
                last = exc
                log.warning("%s on %s failed: %s %s", role, dep, type(exc).__name__, exc)
        raise RuntimeError(f"all deployments failed for {role}: {last}")

    def _timeout(self, moment: Moment) -> float:
        return self.timeout_s * (2.0 if moment.kind in ("half_time", "full_time") else 1.0)

    async def core(self, moment: Moment) -> tuple[Core, str]:
        return await self._run("explainer", core_prompt(moment), Core, self._timeout(moment))

    async def persona(self, moment: Moment, persona: str, langs: list[str], core: Core | None,
                      feedback: list[str]) -> tuple[Explanation, str]:
        return await self._run(persona, persona_prompt(moment, persona, langs, core, feedback), Explanation,
                               self._timeout(moment))
