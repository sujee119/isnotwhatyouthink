"""The guards that keep a script honest, lifted from the reels pipeline.

These exist because of real failures there: drafts that lifted the source's
sentences, hedged findings restated as settled fact, and stock LLM openers.
They are not specific to science, so they carry over unchanged.
"""
from __future__ import annotations

import re

import config

def _shingles(text: str, n: int = 4) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def overlap_ratio(draft: str, source: str, n: int = 4) -> float:
    """Fraction of the draft's n-grams that also appear in the source."""
    d, s = _shingles(draft, n), _shingles(source, n)
    if not d:
        return 0.0
    return len(d & s) / len(d)


IG_CAPTION_LIMIT = 2150   # Instagram's hard cap is 2200

MAX_OVERLAP = 0.12  # above this, the draft is too close to the original


# ------------------------------------------------------------- hype filter

BANNED_PHRASES = [
    "changes everything", "scientists are baffled", "baffled scientists",
    "they never expected", "blow your mind", "you won't believe",
    "you wont believe", "breaking the internet", "game-changer",
    "game changer", "groundbreaking", "the truth about",
    "will shock you", "shocked the world", "doctors hate",
    "nobody saw this coming", "this is huge", "mind-blowing",
    "mind blowing", "insane discovery", "wait for it",
]

# Overclaim patterns: hedged findings stated as settled fact.
OVERCLAIM_PATTERNS = [
    (r"\bproves?\s+that\b", "states proof"),
    (r"\bcures?\b", "claims a cure"),
    (r"\bwill\s+(?:cure|treat|eliminate|end)\b", "promises a future certainty"),
    (r"\bdefinitely\b", "overstated certainty"),
    (r"\bconfirms?\s+that\s+\w+\s+causes?\b", "asserts causation"),
]


def find_banned(text: str) -> list[str]:
    low = text.lower()
    return [p for p in BANNED_PHRASES if p in low]


def find_overclaims(text: str) -> list[str]:
    hits = []
    for pattern, label in OVERCLAIM_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            hits.append(label)
    return hits


# Weakening a claim can never make it less accurate, so overclaims are
# repaired in place rather than thrown away. Retrying on these just burned
# API calls and often came back with the same phrasing.
SOFTENERS = [
    (r"\bproves\s+that\b", "suggests that"),
    (r"\bprove\s+that\b", "suggest that"),
    (r"\bproven\s+to\b", "shown to"),
    (r"\bdefinitely\b", "likely"),
    (r"\bwill\s+cure\b", "might one day help treat"),
    (r"\bwill\s+treat\b", "might one day help treat"),
    (r"\bwill\s+eliminate\b", "might reduce"),
    (r"\bwill\s+end\b", "might help end"),
    (r"\bcures\b", "may help treat"),
    (r"\bcure\b", "potential treatment for"),
    (r"\bconfirms\s+that\b", "indicates that"),
]


def soften(text: str) -> tuple[str, list[str]]:
    """Hedge overclaiming language. Returns (text, what_changed)."""
    changed = []
    for pattern, repl in SOFTENERS:
        new = re.sub(pattern, repl, text, flags=re.I)
        if new != text:
            changed.append(repl)
            text = new
    return text, changed


# Distinctive nouns from the shape examples in the prompt. If one shows up
# in a hook but not in the article, the model echoed the example.
EXAMPLE_TOKENS = [
    "strawberry", "strawberries", "beetles", "uniforms", "fabric",
    "lions", "carcass", "zoo",
    "fossil", "magnetic", "ocean",
]

WEAK_OPENERS = [
    "scientists", "researchers", "a new study", "a study", "in a new",
    "have you ever", "did you know", "imagine", "what if", "picture this",
    "new research", "a team of", "experts", "according to",
    "this is the story", "let me tell you", "here's something",
]


_STOP = {
    "the", "and", "that", "this", "with", "from", "they", "them", "their",
    "what", "when", "which", "there", "here", "then", "than", "been", "were",
    "have", "has", "had", "into", "over", "about", "just", "only", "some",
    "something", "surprising", "study", "research", "new", "found", "team",
    "could", "would", "should", "makes", "think", "things", "really",
    "every", "single", "your", "you", "it", "its", "was", "are", "but",
}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def hook_problems(hook: str, source: str = "") -> list[str]:
    """Catch the hooks that will lose the viewer in two seconds."""
    problems = []
    h = hook.strip()
    low = h.lower()

    if not h:
        return ["The hook is empty."]

    words = h.split()
    if len(words) > 32:
        problems.append(
            f"The hook is {len(words)} words. Cut it to under 25 -- it has "
            f"to land in about five seconds.")
    if len(words) < 7:
        problems.append("The hook is too short to set up a loop. Aim for 12-25 words.")

    for opener in WEAK_OPENERS:
        if low.startswith(opener):
            problems.append(
                f"The hook opens with '{opener}', which reads as a press "
                f"release. Open on the subject or the action instead.")
            break

    first = re.split(r"(?<=[.!?])\s", h)[0]
    if first.rstrip().endswith("?"):
        problems.append(
            "The first sentence is a question. Questions invite scrolling -- "
            "lead with a statement.")

    # The prompt shows example hooks to demonstrate shape. If any of their
    # distinctive subject matter turns up here, the model copied the
    # example instead of reading the article.
    leaked = [t for t in EXAMPLE_TOKENS
              if re.search(rf"\b{t}\b", low) and t not in source.lower()]
    if leaked:
        problems.append(
            f"The hook borrows subject matter from the example hooks "
            f"({', '.join(sorted(set(leaked))[:3])}), which are unrelated "
            f"stories. Build the hook only from this article's own content.")

    # Is the hook grounded in the actual story? Rather than looking for
    # capital letters (which wrongly rejected hooks built on specific
    # lowercase nouns like "three letters in your DNA"), check that it
    # shares real content words with the source material.
    if source:
        shared = _content_words(h) & _content_words(source)
        has_number = bool(re.search(r"\d", h))
