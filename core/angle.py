"""Derive the angle FROM the sources, after they have been read.

The gate approves a subject before anything has been fetched, so any angle
it supplies is the model's recollection rather than a sourced fact. That
broke on the first real topic: the gate said leap seconds end in 2035, the
sources said 2027, and the writer refused rather than pick between "use
this angle" and "only say what the sources support". It was right to.

So the angle is derived here instead, once the sources are in hand. It
cannot contradict them, because it is built from them.

Source disagreement is resolved three ways rather than two:

  agree               state it
  one is out of date  use the newest and do not mention the old one; a
                      stale source is not a controversy, and presenting
                      it as one dresses up a failure to check dates as
                      balance
  genuinely disputed   that becomes the angle -- "two explanations and
                      the argument is still live" -- said with both sides
                      and as the point of the video, not a caveat

And a floor under all three: one source asserting something surprising is
an assertion, not a finding. No second source, no video.
"""
from __future__ import annotations

import json
import re

import config
from core import llm

SYSTEM = """You find the one genuinely surprising, checkable thing in a set
of sources about a single subject, for a thirty-second video whose opening
line is "<SUBJECT> IS NOT WHAT YOU THINK".

You are given the SUBJECT and several SOURCES. Work only from them.

First decide what the sources actually support:

- AGREE: they tell the same story. Use it.
- STALE: they conflict because one is older and the facts changed (a date
  moved, a decision was reversed, a figure was revised). Use the NEWEST
  and ignore the outdated one. Do NOT present this as a dispute -- it is
  not one, and saying "some say X, some say Y" about a superseded fact
  misleads the viewer.
- DISPUTED: they conflict substantively, both are credible, and nothing is
  settled. Then the disagreement IS the angle. Say both sides.
- THIN: only one source supports the surprising claim, or nothing in them
  is actually surprising. There is no video.

Then return the angle.

An angle is good when:
- it overturns something a general viewer actually believes
- it is specific: a number, a date, a mechanism, a consequence
- every part of it can be pointed at in the sources
- it does not depend on a claim about any living person's conduct

An angle is bad when it is a definition read aloud, a vague "more complex
than you think", or anything you had to supply from your own knowledge
because the sources were silent.

Return ONLY valid JSON, no fence:

{"verdict": "agree",
 "angle": "one or two sentences naming the surprise, specific enough that
 a writer cannot drift off it",
 "support": ["the exact sentence or figure from a source that backs it",
 "a second one from a DIFFERENT source"],
 "confidence": 0.0-1.0}

For "disputed", write the angle as both positions and put one supporting
quote from each side in "support".

For "stale", write the angle using the current fact only, and say in
"note" which claim you discarded as outdated.

For "thin", return {"verdict": "thin", "why": "one sentence"}.

"support" must contain at least two entries drawn from at least two
different sources for any verdict except "thin". If you cannot fill it,
the verdict is "thin"."""


class NoAngle(RuntimeError):
    """The sources will not support a surprising, checkable claim."""


def _user(subject: str, sources: list[dict]) -> str:
    parts = [f"SUBJECT: {subject}", "", "SOURCES:"]
    budget = max(900, 9000 // max(1, len(sources)))
    for i, s in enumerate(sources, 1):
        when = s.get("published") or "date unknown"
        parts.append(f"\n--- source {i} | {s['kind']} | {when} | "
                     f"{s['title'][:90]}\n{s['text'][:budget]}")
    return "\n".join(parts)


def derive(subject: str, sources: list[dict]) -> dict:
    """Return {"angle", "verdict", "support", "confidence", "note"}.

    Raises NoAngle when the sources do not support one, which is a reason
    to try the next topic rather than to write something unsupported.
    """
    if len(sources) < 2:
        raise NoAngle(f"only {len(sources)} source(s); a single source is "
                      f"an assertion, not a finding")

    fn = llm.PROVIDERS.get(config.SCRIPT_PROVIDER)
    if fn is None:
        raise NoAngle(f"no such provider: {config.SCRIPT_PROVIDER}")

    raw = fn(SYSTEM, _user(subject, sources))
    raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(),
                 flags=re.M).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NoAngle(f"angle step returned non-JSON: {str(exc)[:80]}")
    if not isinstance(obj, dict):
        raise NoAngle("angle step returned no object")

    verdict = str(obj.get("verdict", "")).lower().strip()
    if verdict == "thin" or not verdict:
        raise NoAngle(str(obj.get("why") or "nothing surprising in the "
                                            "sources")[:160])
    if verdict not in ("agree", "stale", "disputed"):
        raise NoAngle(f"unknown verdict from the angle step: {verdict[:40]}")

    angle = " ".join(str(obj.get("angle", "")).split()).strip()
    # A low floor on purpose. "Leap seconds end in 2027" is five words and
    # perfectly specific, while "it is more complex than you think" is six
    # and says nothing -- length does not measure specificity. The real
    # guard is the two-supporting-quotes rule below: a vague claim cannot
    # be backed by two quotes from two sources.
    if len(angle.split()) < 4:
        raise NoAngle("the angle came back too short to write to")

    support = [" ".join(str(s).split()) for s in (obj.get("support") or [])
               if str(s).strip()]
    if len(support) < 2:
        # The two-source rule is the whole point: it is what would have
        # caught the 2035 figure, which no source actually contained.
        raise NoAngle(f"verdict '{verdict}' but only {len(support)} "
                      f"supporting quote(s); needs two from two sources")

    try:
        conf = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0

    return {
        "angle": angle,
        "verdict": verdict,
        "support": support[:4],
        "confidence": max(0.0, min(1.0, conf)),
        "note": " ".join(str(obj.get("note", "")).split())[:200],
    }
