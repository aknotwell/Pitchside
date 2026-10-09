"""Code-first verification of model-written explanations.

A model never grades its own work. Every number in an explanation must trace
to the moment's evidence pack (or to the clock and score), every evidence key
it cites must exist, and it may only name the people and clubs the moment
allows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from matchlens.contracts import Moment

NUM_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)")
BANNED = ("odds", "bet ", "betting", "apuesta", "cuota", "賭", "オッズ")
MAX_TITLE = 70
MAX_TEXT = 260


class Variant(BaseModel):
    persona: str = Field(description="'analyst' or 'casual'")
    lang: str = Field(description="ISO 639-1 language code")
    title: str = Field(description=f"Overlay headline, at most {MAX_TITLE} characters")
    text: str = Field(description=f"One or two sentences explaining why it matters, at most {MAX_TEXT} characters")
    evidence_keys: list[str] = Field(description="Keys of the evidence items this variant relies on")


class Explanation(BaseModel):
    variants: list[Variant]


@dataclass
class VerifyResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def allowed_numbers(moment: Moment) -> list[float]:
    nums: list[float] = []
    for e in moment.evidence:
        nums += [float(n.replace(",", ".")) for n in NUM_RE.findall(e.label)]  # e.g. "last 10 min"
        vals = [e.value, e.before, e.after]
        for v in vals:
            if isinstance(v, (int, float)):
                nums.append(float(v))
                if 0 < abs(float(v)) <= 1:
                    nums.append(round(float(v) * 100, 1))  # 0.83 -> 83 (%)
            elif isinstance(v, str):
                nums += [float(n.replace(",", ".")) for n in NUM_RE.findall(v)]
        if isinstance(e.before, (int, float)) and isinstance(e.after, (int, float)):
            nums.append(abs(float(e.after) - float(e.before)))
    nums += [float(v) for v in moment.score.values()]
    minute = int(moment.clock_s // 60)
    nums += [minute, minute + 1, 45, 90, 1, 2]  # clock references and "first/second half"
    return nums


def _matches(n: float, allowed: list[float]) -> bool:
    for a in allowed:
        tol = max(0.051, abs(a) * 0.02)
        if abs(n - a) <= tol or abs(n - round(a)) < 1e-9 or abs(n - round(a, 1)) < 1e-9:
            return True
    return False


def verify(expl: Explanation, moment: Moment, required: list[tuple[str, str]], roster_names: set[str]) -> VerifyResult:
    problems: list[str] = []
    allowed = allowed_numbers(moment)
    evidence_keys = {e.key for e in moment.evidence}
    allowed_names = set(moment.names.values())
    forbidden_names = {n for n in roster_names if n not in allowed_names}
    have = {(v.persona, v.lang) for v in expl.variants}
    for persona, lang in required:
        if (persona, lang) not in have:
            problems.append(f"missing variant persona={persona} lang={lang}")
    for v in expl.variants:
        tag = f"[{v.persona}/{v.lang}]"
        body = f"{v.title} {v.text}"
        if len(v.title) > MAX_TITLE:
            problems.append(f"{tag} title longer than {MAX_TITLE} characters")
        if len(v.text) > MAX_TEXT:
            problems.append(f"{tag} text longer than {MAX_TEXT} characters")
        if not v.evidence_keys:
            problems.append(f"{tag} cites no evidence keys")
        for k in v.evidence_keys:
            if k not in evidence_keys:
                problems.append(f"{tag} cites unknown evidence key '{k}'")
        for raw in NUM_RE.findall(body):
            n = float(raw.replace(",", "."))
            if not _matches(n, allowed):
                problems.append(f"{tag} number {raw} is not in the evidence pack")
        for name in forbidden_names:
            last = name.split()[-1]
            if name in body or (len(last) > 3 and re.search(rf"\b{re.escape(last)}\b", body)):
                problems.append(f"{tag} names '{name}', who is not part of this moment")
        low = body.lower()
        for b in BANNED:
            if b in low:
                problems.append(f"{tag} contains banned betting language '{b.strip()}'")
    return VerifyResult(ok=not problems, problems=problems)
