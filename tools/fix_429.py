#!/usr/bin/env python3
"""Two fixes the 20 Oct 09:33 run exposed.

    [2/7] pool   wikipedia FAILED HTTPError; reddit eli5 HTTP 429
          to the gate: 12 trend, 32 other
    [3/7] gate
          ! HTTP Error 429: Too Many Requests
          the gate decides what may be built, so nothing is built

1. THE GATE HAD NO 429 RETRY. A 429 is the free tier's per-minute cap and
   it is transient, but _call_gemini re-raises anything that is not a 404
   -- immediately, with no wait. The gate is ONE batched call covering
   the whole shortlist, so losing it to a momentary 429 costs the entire
   slot and the day's video. core/tts.py has waited this out for the same
   reason all along; this path never did. Now: three attempts, 25s then
   50s.

2. raising the non-trend slots DID NOTHING. gate.review's signature is

       def review(candidates, limit: int = 20)

   and its body does candidates[:limit]. So the 44 assembled candidates
   were sliced straight back to the first 20 -- the same 12 trends and 8
   others as before. The log line "to the gate: 12 trend, 32 other"
   describes what was assembled, not what the gate saw. Passing limit
   explicitly is what actually widens the intake.

Safe to re-run: reports "already patched" and changes nothing.

    python3 tools/fix_429.py
"""
import ast
import pathlib
import sys

LLM_EDITS = [(
    """    last = None
    for model in models[:6]:
        try:
            return _gemini_once(model, system, user)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            last = exc
            print(f"  ! model '{model}' not available, trying next")
            if model in _GEMINI_MODEL_CACHE:
                _GEMINI_MODEL_CACHE.remove(model)
    raise last or RuntimeError("no usable Gemini model")
""",
    '''    last = None
    for model in models[:6]:
        for attempt in (1, 2, 3):
            try:
                return _gemini_once(model, system, user)
            except urllib.error.HTTPError as exc:
                # 429 is the free tier's per-minute cap, and it is
                # transient. The gate is ONE call covering the whole
                # shortlist, so losing it to a momentary 429 costs the
                # entire slot and the day's video -- which is what the
                # 20 Oct 09:33 run did. core/tts.py has waited this out
                # for the same reason all along; this path never did.
                if exc.code == 429:
                    if attempt < 3:
                        wait = 25 * attempt
                        print(f"  ! gemini rate-limited (429), waiting "
                              f"{wait}s -- attempt {attempt} of 3")
                        time.sleep(wait)
                        continue
                    last = exc
                    break
                if exc.code != 404:
                    raise
                last = exc
                print(f"  ! model '{model}' not available, trying next")
                if model in _GEMINI_MODEL_CACHE:
                    _GEMINI_MODEL_CACHE.remove(model)
                break
    raise last or RuntimeError("no usable Gemini model")
''',
    "gemini rate-limited (429), waiting")]

RUN_EDITS = [(
    """        approved = gate.review(candidates)
""",
    """        # limit= is load-bearing: review() defaults to limit=20 and
        # slices candidates[:limit], so without it the 32 non-trend
        # candidates assembled above are truncated straight back to 8
        # and widening the slots changes nothing at all.
        approved = gate.review(candidates, limit=len(candidates))
""",
    "limit=len(candidates)")]


def apply(path: pathlib.Path, edits: list) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = original = path.read_text()
    applied = 0

    for old, new, marker in edits:
        if marker in src:
            continue
        n = src.count(old)
        if n == 0:
            return (f"  {path}: expected text missing -- NOT TOUCHED "
                    f"(right repo? earlier patches applied?)")
        if n > 1:
            return f"  {path}: target appears {n}x -- NOT TOUCHED"
        src = src.replace(old, new)
        applied += 1

    if applied == 0:
        return f"  {path}: already patched"

    try:
        ast.parse(src)
    except SyntaxError as exc:
        return f"  {path}: would not parse ({exc}) -- NOT WRITTEN"

    replaced = {l for old, _, _ in edits for l in old.splitlines()}
    lost = {l for l in set(original.splitlines()) - set(src.splitlines())
            if l.strip()} - replaced
    if lost:
        return (f"  {path}: would drop {len(lost)} unrelated line(s) "
                f"-- NOT WRITTEN")

    path.write_text(src)
    return f"  {path}: patched {applied} place(s)"


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/gate.py").exists():
        sys.exit("shorts repo only (~/singlescape/isnotwhatyouthink) "
                 "-- no core/gate.py here")
    print("surviving a rate limit, and actually widening the gate:")
    print(apply(root / "core/llm.py", LLM_EDITS))
    print(apply(root / "run.py", RUN_EDITS))


if __name__ == "__main__":
    main()
