#!/usr/bin/env python3
"""Three corrections from reading an actual shortlist.

1. "AMELIA EARHART IS NOT WHAT YOU THINK" -- she is a person, so it
   should be WHO. Ansel Adams two rows below got it right, which
   narrowed it down: her description is

       American aviation pioneer (1897-disappeared 1937)

   and DECEASED only matched a bare "(YYYY-YYYY)". The word between the
   dash and the year broke it, "pioneer" is not in the occupation list,
   and there is no "born", so all three person signals missed.

   This is not only cosmetic. deceased_long_ago() is what DROPs a
   recent death, and it reads the same regex -- so a description like
   "(1950-disappeared 2024)" would have sailed past the recent-death
   filter as well.

2. "ANCIENT CAPPADOCIAN LANGUAGE IS NOT WHAT YOU THINK" is three words
   of qualifier around one recognisable idea. The gate now trims to the
   shortest form that still names the thing.

3. YouTube, ChatGPT and Instagram ranked in the shortlist. They are
   famous, which is what the new familiarity score rewards -- but fame
   is not the whole test. A platform everybody uses and the press
   covers daily has nothing hidden left to reveal, so the gate now
   rejects them on quality, where films and album releases already sit.

    python3 tools/fix_hook2.py
"""
import ast
import pathlib
import sys

POOL_EDITS = [(
    r'''DECEASED = re.compile(r"\((?:c\.\s*)?(\d{3,4})\s*[-–—]\s*(\d{3,4})\)")
''',
    r'''# The second date may carry a word: Wikipedia writes "disappeared",
# "died", "d." and "c." inside the brackets. Without this, Amelia
# Earhart's "(1897-disappeared 1937)" matched nothing, so she was not a
# person (hook said WHAT, not WHO) and -- the part that matters -- a
# recent death written the same way would skip the DROP entirely.
DECEASED = re.compile(
    r"\((?:c\.\s*)?(\d{3,4})\s*[-–—]\s*"
    r"(?:disappeared\s+|died\s+|d\.\s*|c\.\s*|after\s+|before\s+)?"
    r"(\d{3,4})\)")
''',
    "disappeared\\s+|died\\s+", 1)]

GATE_EDITS = [
    ('''  familiar name over the formally correct one -- "The first electric
  car", not "La Jamais Contente".
''',
     '''  familiar name over the formally correct one -- "The first electric
  car", not "La Jamais Contente". Strip qualifiers nobody says aloud:
  "Ancient Cappadocian language" is "Cappadocian", "Domestic cat" is
  "Cats". Keep the shortest form that still names the thing.
''',
     "Strip qualifiers nobody says aloud", 1),

    ('''- a film, series, game, album or event release -- traffic without content
''',
     '''- a film, series, game, album or event release -- traffic without content
- a dominant present-day tech platform, app or AI product: YouTube,
  ChatGPT, Instagram and their like. Famous is necessary but not
  sufficient -- something everybody uses and the press writes about
  weekly has no hidden thing left to reveal, and the video would be a
  description of a product
''',
     "dominant present-day tech platform", 1),
]


def apply(path: pathlib.Path, edits: list) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = original = path.read_text()
    applied = 0
    for old, new, marker, count in edits:
        if marker in src:
            continue
        n = src.count(old)
        if n != count:
            return (f"  {path}: expected {count} occurrence(s), found {n} "
                    f"-- NOT TOUCHED")
        src = src.replace(old, new)
        applied += 1
    if applied == 0:
        return f"  {path}: already patched"
    try:
        ast.parse(src)
    except SyntaxError as exc:
        return f"  {path}: would not parse ({exc}) -- NOT WRITTEN"
    replaced = {l for old, _, _, _ in edits for l in old.splitlines()}
    lost = {l for l in set(original.splitlines()) - set(src.splitlines())
            if l.strip()} - replaced
    if lost:
        return f"  {path}: would drop {len(lost)} unrelated line(s) -- NOT WRITTEN"
    path.write_text(src)
    return f"  {path}: patched {applied} place(s)"


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/gate.py").exists():
        sys.exit("shorts repo only (~/singlescape/isnotwhatyouthink)")
    if "RECOGNISES ON SIGHT" not in (root / "core/gate.py").read_text():
        sys.exit("run tools/fix_famous.py first -- this builds on it")
    print("person detection, subject trimming, and platform rejects:")
    print(apply(root / "core/pool.py", POOL_EDITS))
    print(apply(root / "core/gate.py", GATE_EDITS))


if __name__ == "__main__":
    main()
