"""Turn one approved topic into a thirty-second script.

The channel has exactly one structural rule: the first spoken words are
"<SUBJECT> IS NOT WHAT YOU THINK" (or WHO, for a person). Everything after
it has to earn that claim, which is the whole difficulty -- a hook that
promises a reversal and then delivers a definition is worse than no hook.

So the gate's `angle` is passed in and the script is held to it. The
writer cannot choose a different surprise, and cannot decide there isn't
one; if the angle will not support the claim, generation fails and the run
makes no video rather than a dishonest one.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import config
from core import guards, llm

SECTIONS = ("hook", "turn", "proof", "button")


@dataclass
class Script:
    hook: str            # the fixed line, verbatim
    turn: str            # what people assume, and the pivot away from it
    proof: str           # the specific, checkable fact that does the work
    button: str          # one closing line
    narration: str
    title: str           # the YouTube title
    description: str
    cover_text: str      # burned over the opening -- always the hook
    shots: list = field(default_factory=list)
    tags: list = field(default_factory=list)

    def word_count(self) -> int:
        return len(self.narration.split())


SYSTEM_PROMPT = """You write {total_seconds}-second vertical video scripts for a
channel called "is not what you think". Every video takes one subject and
overturns what a viewer assumes about it, using a fact that is specific,
checkable and genuinely surprising.

You will be given a SUBJECT, the exact ANGLE the video must deliver, and
SOURCE MATERIAL. You may only use what the sources support.

== THE ONE RULE ==

The narration's first words are exactly, character for character:

    {hook}

Do not reword it, do not add to it, do not put anything before it. It is
the channel's name and its promise.

== THE SHAPE ==

HOOK ({hook_words} words, fixed)
  The line above, alone. Nothing else.

TURN (about {turn_words} words)
  Name the assumption the viewer actually holds -- in their words, not a
  strawman -- then pivot. "You picture X. It was closer to Y."
  This is where most scripts fail: a vague "but there's more to it" is not
  a turn. The turn must say what is wrong with the assumption.

PROOF (about {proof_words} words)
  The specific thing that makes the reversal true. A number, a date, a
  mechanism, a quotation, a consequence. If you cannot be specific here,
  the angle does not work -- say so (see FAILING, below) rather than
  padding.

BUTTON (about {button_words} words)
  One line that lands it. No rhetorical question.
{cta_rule}

== RULES ==

1. TOTAL LENGTH: target {target_words} words including the hook. Hard
   ceiling {max_words}. Every sentence must carry new information.

2. ORIGINAL WORDING. The sources are copyrighted. Never copy more than
   three consecutive words from them. Re-explain from scratch.

3. ONLY WHAT THE SOURCES SAY. No guessing, no "experts believe", no
   filling a gap with something plausible. If the sources hedge, you
   hedge.

4. NO REAL PEOPLE'S CONDUCT. You may state what a long-dead historical
   figure did, as a matter of record. Never anyone's crimes, illness,
   death, family or character -- and nothing at all about a living
   person.

5. NO ADVICE. Not medical, legal or financial. Explaining is not advising.

6. SPOKEN ENGLISH. Short sentences. Vary the length. Fragments are fine.
   No stock openers: no "In a world where", no "Picture this", no
   "What if I told you".

== THE SHOT LIST ==

Supply {shots_min}-{shots_max} shots covering the whole script. Each is a
stock-image search term plus the exact narration phrase it appears on.

- "cue" is 3-5 words copied VERBATIM from your own narration, in order,
  spread across the whole script rather than clustered at the start.
- "query" is 2-4 plain words naming a photograph that actually exists.
  "brass ship chronometer" exists. "the concept of time" does not.
- Never reuse a query.

== FAILING ==

If the sources will not support the angle, or the only honest version is
not surprising, return exactly:

{{"refuse": "one sentence saying what is missing"}}

A refusal costs one video. A script that invents its surprise costs the
channel.

== OUTPUT ==

Return ONLY valid JSON, no markdown fence:
{{
  "hook":   "the fixed line, verbatim",
  "turn":   "...",
  "proof":  "...",
  "button": "...",
  "title":  "a YouTube title under 90 characters, not identical to the hook",
  "description": "2-3 sentences, worded differently from the narration",
  "tags": ["4-6 lowercase tags, no # symbol"],
  "shots": [{{"cue": "3-5 verbatim words", "query": "stock photo term"}}]
}}
"""


class ScriptRefused(RuntimeError):
    """The writer declined the angle, or could not pass the guards."""


def _user_prompt(item: dict, sources: list[dict]) -> str:
    parts = [
        f"SUBJECT: {item['subject']}",
        f"ANGLE YOU MUST DELIVER: {item.get('angle', '')}",
        "",
        "SOURCE MATERIAL (copyrighted -- do not reuse its wording):",
    ]
    budget = 9000
    for i, s in enumerate(sources, 1):
        text = s["text"][:max(600, budget // max(1, len(sources)))]
        parts.append(f"\n--- source {i} ({s['kind']}): {s['title'][:90]}\n{text}")
    return "\n".join(parts)


def _sizes() -> dict:
    total = max(12, int(config.TARGET_SECONDS))
    wps = config.WORDS_PER_SECOND
    target_words = int(total * wps)
    return {
        "total_seconds": total,
        "target_words": target_words,
        "max_words": int(config.MAX_SECONDS * wps),
        "turn_words": max(10, round(target_words * 0.30)),
        "proof_words": max(12, round(target_words * 0.40)),
        "button_words": max(5, round(target_words * 0.12)),
        "shots_min": max(4, round(total / 3.8)),
        "shots_max": max(6, round(total / 2.6)),
        # The call to action lives on the end card by default, so the
        # narration is told to stay out of it.
        "cta_rule": ("  Then one short closing line inviting a follow -- "
                     "keep it under six words."
                     if config.SPOKEN_CTA else
                     '  No call to action, no "follow for more". The video '
                     'ends on this line and loops straight back into the '
                     'hook, so make it land.'),
    }


def _clean_shots(raw, narration: str) -> list[dict]:
    """Keep only shots whose cue really appears in the narration."""
    low = re.sub(r"\s+", " ", narration.lower())
    out, seen = [], set()
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        cue = " ".join(str(s.get("cue", "")).split())
        query = " ".join(str(s.get("query", "")).split())
        if not cue or not query:
            continue
        if re.sub(r"\s+", " ", cue.lower()) not in low:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"cue": cue, "query": query})
    return out


def generate(item: dict, sources: list[dict], attempts: int = 3) -> Script:
    """Write the script for one approved topic.

    Raises ScriptRefused rather than returning something weak: a run that
    makes no video is recoverable, a bad video on the channel is not.
    """
    if not sources:
        raise ScriptRefused("no usable sources for this topic")

    hook = item.get("hook") or ""
    if not hook:
        raise ScriptRefused("the topic has no hook line")

    sizes = _sizes()
    sizes["hook"] = hook
    sizes["hook_words"] = len(hook.split())
    system = SYSTEM_PROMPT.format(**sizes)
    user = _user_prompt(item, sources)
    source_text = " ".join(s["text"] for s in sources)

    fn = llm.call          # the whole provider chain, not one name

    last = ""
    for attempt in range(1, attempts + 1):
        try:
            raw = fn(system, user)
        except Exception as exc:
            last = f"provider error: {str(exc)[:120]}"
            if attempt < attempts:
                delay = min(45, 4 * (2 ** (attempt - 1)))
                print(f"    {last}; waiting {delay}s")
                time.sleep(delay)
            continue

        raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(),
                     flags=re.M).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            last = f"not JSON ({str(exc)[:60]})"
            user = _user_prompt(item, sources) + (
                "\n\nYour last reply was not valid JSON. Return only the "
                "JSON object described above.")
            continue

        if obj.get("refuse"):
            raise ScriptRefused(f"writer declined: {str(obj['refuse'])[:160]}")

        missing = [k for k in SECTIONS if not str(obj.get(k, "")).strip()]
        if missing:
            last = f"empty sections: {', '.join(missing)}"
            user += f"\n\nThese sections were empty: {', '.join(missing)}."
            continue

        # The hook is not negotiable.
        got_hook = " ".join(str(obj["hook"]).split()).rstrip(".").upper()
        if got_hook != hook.rstrip(".").upper():
            last = "the hook was reworded"
            user += (f"\n\nYou changed the first line. It must be exactly: "
                     f"{hook}")
            continue
        obj["hook"] = hook

        narration = " ".join(str(obj.get(k, "")).strip() for k in SECTIONS)
        narration = re.sub(r"\s+", " ", narration).strip()

        narration, softened = guards.soften(narration)
        if softened:
            print(f"    softened {len(softened)} overclaim(s)")

        problems = []
        words = len(narration.split())
        if words > sizes["max_words"]:
            problems.append(f"too long at {words} words; cut to "
                            f"{sizes['target_words']}")
        # Length is controlled here rather than by stretching the audio.
        # At 3 words/sec this band keeps finished videos near thirty
        # seconds without ever distorting the delivery to hit a number.
        elif words < sizes["target_words"] * 0.90:
            problems.append(f"too short at {words} words; aim for "
                            f"{sizes['target_words']}")

        ratio = guards.overlap_ratio(narration, source_text)
        if ratio > 0.12:
            problems.append(f"{ratio:.0%} of this is lifted from the "
                            f"sources; re-explain it in your own words")

        banned = guards.find_banned(narration)
        if banned:
            problems.append(f"stock phrasing: {', '.join(banned[:3])}")

        shots = _clean_shots(obj.get("shots"), narration)
        # The ask is shots_min-shots_max, but the floor for acceptance is
        # lower: the renderer copes with fewer by holding stills longer,
        # and a retry costs a call on a rate-limited free tier.
        if len(shots) < max(4, sizes["shots_min"] - 3):
            problems.append(
                f"only {len(shots)} usable shots; give "
                f"{sizes['shots_min']}-{sizes['shots_max']}, each cue "
                f"copied verbatim from your narration")

        if problems:
            last = "; ".join(problems)[:200]
            print(f"    attempt {attempt}: {last[:140]}")
            user += "\n\nFix these and return the JSON again:\n- " + \
                    "\n- ".join(problems)
            continue

        title = " ".join(str(obj.get("title") or "").split())[:90] or hook
        return Script(
            hook=hook,
            turn=str(obj["turn"]).strip(),
            proof=str(obj["proof"]).strip(),
            button=str(obj["button"]).strip(),
            narration=narration,
            title=title,
            description=" ".join(str(obj.get("description") or "").split()),
            cover_text=hook,
            shots=shots,
            tags=[str(t).lower().lstrip("#")[:30]
                  for t in (obj.get("tags") or [])][:6],
        )

    raise ScriptRefused(f"gave up after {attempts} attempts: {last}")
