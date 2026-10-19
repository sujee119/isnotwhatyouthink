"""The gate: nothing gets built that this has not passed.

core.pool ranks and hard-drops with regexes. Regexes are not judgement --
they put three recently dead people at the top of the pool and let a claim
about a living actress through, because a word list cannot tell a
historical figure from someone who died last week, or a concept from a
person. So every candidate passes through here first.

One batched call classifies the whole shortlist, because the free tier is
rate-limited and one call for twenty topics is both cheaper and more
consistent than twenty calls.

The gate fails CLOSED. If the LLM is unreachable, or returns nonsense, or
the key is missing, nothing is approved and the run makes no video. A
silent day is a cost; a video about someone who just died is not
recoverable.
"""
from __future__ import annotations

import json
import re

import config
from core import llm

VERDICTS = ("approve", "reject")

SYSTEM = """You are the safety and quality gate for a short-video channel
called "is not what you think". Each video takes ONE subject and explains
something genuinely surprising about it in about thirty seconds.

You are given a numbered list of candidate subjects. For each one, return
a verdict.

REJECT, always:
- any living person, and anyone who died within the last twenty years
- anyone's death, illness, crime, arrest, trial, lawsuit or accusation
- war, terrorism, disasters, missing people, atrocities
- elections, parties, politicians, legislation, protests, border disputes
- a named company or person's wrongdoing or controversy
- medical or financial advice, diagnoses, treatments, cures
- anything sexual, anything involving minors, anything age-restricted
  (alcohol, tobacco, gambling, drugs)

REJECT, and this one has no exceptions: the subject may never be a
COUNTRY, a nationality, an ethnic group, an indigenous people, a tribe, a
caste, a race, a religion or its followers, a political movement, or any
other group of people. "<Nationality> is not what you think" is an insult
with a hook on the front, no matter how careful the facts are. Also reject
colonialism, slavery, genocide, apartheid, partitions, coups, wars of
independence, border disputes and reparations as subjects.

A physical thing from a period of history is fine -- Roman concrete, the
Rosetta Stone, a Viking sunstone, a medieval recipe. The dividing line is
that an object, a technique or a natural phenomenon can be surprising
without characterising anybody; a nation or a people cannot.

- religion as history is still rejected when the subject is the faith or
  its followers; a specific artefact or building is fine

REJECT also, on quality:
- a film, series, game, album or event release -- traffic without content
- a country, state, city or military base as a subject
- a navigational query: a login page, a score, a schedule, a lottery draw
- a subject with nothing surprising to say, where the video would be a
  definition read aloud
- a subject you do not actually know a surprising, checkable fact about

APPROVE only when all of these hold:
- the subject is a thing, a phenomenon, an animal, a place-free concept,
  a technology, a historical event, or a person dead more than a century
- there is a specific, checkable, genuinely counterintuitive fact about it
- a viewer finishing the video would know something they did not know
- nothing in it could harm, defame or mislead anyone

For every APPROVE, also return:
- "is_person": true only for a long-dead historical figure, false otherwise
  (this picks "IS NOT WHO YOU THINK" over "IS NOT WHAT YOU THINK")
- "subject": the subject as it should appear in the hook, 1-4 words, no
  parentheses, no leading article
- "confidence": 0-1, how likely it is that there is a real, checkable
  surprise to be found about this subject

Do NOT supply the angle. You have not read any sources -- only a title and
a one-line description -- so any specific claim you make here would be
recollection, and recollection contradicts the sources. That is exactly
what happened once: this step asserted a date that no source contained,
and the writer had to refuse. The angle is derived later, from the
sources themselves. Your job is whether the subject is SAFE and whether
it is PROMISING, nothing more.

Return ONLY valid JSON, no markdown fence:
{"verdicts": [
  {"n": 1, "verdict": "approve", "is_person": false,
   "subject": "Daylight saving time", "confidence": 0.9},
  {"n": 2, "verdict": "reject", "reason": "living person"}
]}

Every candidate must appear exactly once, by its number. When in doubt,
reject: a missed good topic costs one video, a bad one costs the channel.
"""


class GateUnavailable(RuntimeError):
    """The gate could not run, so nothing may be approved."""


def _ask(candidates: list[dict]) -> dict:
    lines = []
    for n, it in enumerate(candidates, 1):
        bits = [f'{n}. "{it["topic"]}"']
        if it.get("detail"):
            bits.append(f"   wikipedia says: {it['detail']}")
        if it.get("extract"):
            bits.append(f"   first lines: {it['extract'][:240]}")
        bits.append(f"   source: {it['source']}")
        if it.get("why"):
            bits.append(f"   the regex layer flagged: {it['why']}")
        lines.append("\n".join(bits))

    provider = config.SCRIPT_PROVIDER
    fn = llm.PROVIDERS.get(provider)
    if fn is None:
        raise GateUnavailable(f"no such LLM provider: {provider}")

    raw = fn(SYSTEM, "CANDIDATES\n\n" + "\n\n".join(lines))
    raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(),
                 flags=re.M).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GateUnavailable(f"gate returned non-JSON: {str(exc)[:90]}")
    if not isinstance(obj, dict) or not isinstance(obj.get("verdicts"), list):
        raise GateUnavailable(f"gate returned no verdicts: {str(obj)[:120]}")
    return obj


def review(candidates: list[dict], limit: int = 20) -> list[dict]:
    """Return the approved candidates, best first, enriched by the gate.

    Raises GateUnavailable rather than approving anything on failure.
    """
    if not config.GATE_ENABLED:
        raise GateUnavailable(
            "GATE_ENABLED is off. Nothing is approved without the gate -- "
            "turn it back on rather than publishing unreviewed topics.")
    batch = candidates[:limit]
    if not batch:
        return []

    obj = _ask(batch)

    by_n: dict[int, dict] = {}
    for v in obj["verdicts"]:
        if not isinstance(v, dict):
            continue
        try:
            n = int(v.get("n", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(batch):
            by_n[n] = v

    missing = [n for n in range(1, len(batch) + 1) if n not in by_n]
    if missing:
        print(f"  ! the gate skipped {len(missing)} candidate(s); "
              f"treating them as rejected")

    approved: list[dict] = []
    for n, it in enumerate(batch, 1):
        v = by_n.get(n)
        if not v or str(v.get("verdict", "")).lower() != "approve":
            it["gate"] = "reject"
            it["gate_reason"] = (str(v.get("reason", "")) if v
                                 else "no verdict returned")[:120]
            continue

        subject = " ".join(str(v.get("subject") or it["subject"]).split())
        subject = re.sub(r"\s*\([^)]*\)\s*", " ", subject).strip(" ,.:;-")
        if not subject or len(subject.split()) > 6:
            it["gate"] = "reject"
            it["gate_reason"] = "approved without a usable subject"
            continue

        try:
            conf = float(v.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0

        it["gate"] = "approve"
        it["subject"] = subject
        it["is_person"] = bool(v.get("is_person", False))
        it["hook"] = hook_for(subject, it["is_person"])
        # No angle here by design -- core.angle derives it from the
        # sources once they have been read.
        it["confidence"] = conf
        it["score"] = it.get("score", 0.0) + 2.0 * conf
        approved.append(it)

    approved.sort(key=lambda i: -i["score"])
    return approved


def hook_for(subject: str, is_person: bool) -> str:
    return config.HOOK_TEMPLATE.format(
        subject=subject.upper(), wh="WHO" if is_person else "WHAT").upper()
