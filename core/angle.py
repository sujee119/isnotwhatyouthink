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

Every entry in "support" must be text you can point at IN THE SOURCES --
quoted or closely paraphrased. Each one is checked against the source
text automatically, and any that cannot be found is discarded. If none
survive, the topic is dropped. So do not write support from memory: if
the sources do not contain it, leave it out and return "thin".

One substantial source is enough. A long encyclopedia article is not a
single assertion -- two passages from different parts of it support an
angle perfectly well. For "disputed" you need both sides quotable."""


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


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _shingles(text: str, n: int = 5) -> set:
    w = _norm(text).split()
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "it", "its", "this", "that", "these", "those", "which", "who",
    "not", "no", "all", "any", "some", "more", "most", "than", "then",
    "so", "can", "could", "would", "will", "has", "have", "had", "do",
    "does", "did", "while", "when", "where", "there", "their", "them",
    "they", "he", "she", "his", "her", "about", "into", "over", "also",
}


def quote_found(quote: str, blob: str, blob_words: set | None = None) -> bool:
    """Is this supporting quote actually in the sources?

    Checked in code rather than trusted, because this is the guard that
    matters: counting sources could never have caught the 2035 figure, as
    no source contained it.

    Word-run matching turned out useless here -- a genuine paraphrase
    scored 0.14 to 0.44 and an invented claim 0.00, so no threshold
    separated them safely. Content-word recall does, and numbers are
    checked EXACTLY, because a fabricated date or figure is both the most
    likely invention and the most damaging.
    """
    q = _norm(quote)
    if len(q.split()) < 3:
        return False
    if q in blob:
        return True

    # Any number in the quote must appear in the sources. This alone is
    # what rejects "leap seconds end in 2035".
    src_nums = set(re.findall(r"\d+", blob))
    if set(re.findall(r"\d+", q)) - src_nums:
        return False

    words = {w for w in q.split() if len(w) > 2 and w not in _STOP}
    if len(words) < 3:
        return False
    if blob_words is None:
        blob_words = set(blob.split())
    return len(words & blob_words) / len(words) >= 0.7


def derive(subject: str, sources: list[dict]) -> dict:
    """Return {"angle", "verdict", "support", "confidence", "note"}.

    Raises NoAngle when the sources do not support one, which is a reason
    to try the next topic rather than to write something unsupported.
    """
    # One source is fine when it is substantial. Requiring two killed
    # every good topic on the first real run: Leap second, Heraldic
    # hatching and Limonene are encyclopedia subjects, so a news search
    # for them returns nothing and Wikipedia is legitimately the only
    # source. A 5,000-word cited article is not "a single assertion".
    words = sum(len(s["text"].split()) for s in sources)
    if not sources or words < 300:
        raise NoAngle(f"only {words} words across {len(sources)} source(s)")

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

    raw_support = [" ".join(str(s).split()) for s in (obj.get("support") or [])
                   if str(s).strip()]
    if not raw_support:
        raise NoAngle(f"verdict '{verdict}' with no supporting quote")

    # Every quote is checked against the actual source text. This is the
    # real guard: an angle whose support cannot be found in the sources is
    # the model's recollection, which is exactly the 2035 failure.
    blob = _norm(" ".join(s["text"] for s in sources))
    blob_words = set(blob.split())
    support = [q for q in raw_support
               if quote_found(q, blob, blob_words)]
    invented = len(raw_support) - len(support)
    if not support:
        raise NoAngle(
            f"none of the {len(raw_support)} supporting quote(s) appear in "
            f"the sources -- the angle was recalled, not read")
    if verdict == "disputed" and len(support) < 2:
        # Both sides have to be quotable, or it is not a real dispute.
        raise NoAngle("verdict 'disputed' but only one side is in the "
                      "sources")

    try:
        conf = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0

    if invented:
        # Not fatal when at least one quote checked out, but it is a
        # reliability signal worth carrying into the manifest.
        print(f"      ! {invented} supporting quote(s) were not in the "
              f"sources and were discarded")

    return {
        "angle": angle,
        "verdict": verdict,
        "support": support[:4],
        "unverified_quotes": invented,
        "confidence": max(0.0, min(1.0, conf)),
        "note": " ".join(str(obj.get("note", "")).split())[:200],
    }
