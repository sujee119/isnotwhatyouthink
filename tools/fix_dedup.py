#!/usr/bin/env python3
"""Record a used topic under every name it could return under.

mark_used() stored only the POOL topic. dedup_key sorts the words, so a
Reddit title keys as

    "why are katanas worn this way"  ->  "are katanas this way why worn"

and the subject the video was actually about -- "Katana" -> "katana" --
was never recorded at all. The next time Katana arrives from Wikipedia
or the vital-articles source, nothing blocks it and the channel makes
the same video twice.

Four of the six entries in used.json are in this state right now:
Katana, the Eiffel Tower, mitochondria and La Jamais Contente are all
stored under word-bags that their canonical names cannot match.

Three changes:

  * mark_used() takes several names and records each.
  * gather() derives the subject BEFORE the used check and tests both
    keys, so either name blocks a repeat.
  * run.py passes the subject alongside the topic at all four sites.

Then it backfills: every subject and topic already in state/pending is
added to used.json, so the videos you have already made are protected
from the moment this runs.

    python3 tools/fix_dedup.py
"""
import ast
import json
import pathlib
import sys

POOL_EDITS = [
    ('''def mark_used(topic: str) -> None:
    used = _used()
    used.add(dedup_key(topic))
    config.USED_FILE.write_text(json.dumps(sorted(used), indent=0))
''',
     '''def mark_used(*names: str) -> None:
    """Record every name this subject could come back under.

    The pool topic and the hook subject are usually different strings
    -- a Reddit question against "Katana" -- and dedup_key sorts words,
    so recording one leaves the other free to be built again tomorrow.
    """
    used = _used()
    for n in names:
        key = dedup_key(n or "")
        if key:
            used.add(key)
    config.USED_FILE.write_text(json.dumps(sorted(used), indent=0))
''',
     "def mark_used(*names: str)", 1),

    ('''    for it in best.values():
        if dedup_key(it["topic"]) in already:
            repeats += 1
            continue
        it["verdict"], it["why"] = classify(it)
        it["subject"] = hook_subject(it["topic"], it["source"])
''',
     '''    for it in best.values():
        # The subject is derived BEFORE the used check, and both keys
        # are tested. A Reddit title and the subject it becomes are
        # different strings, so checking only the topic let the same
        # subject through under a second name.
        it["subject"] = hook_subject(it["topic"], it["source"])
        if (dedup_key(it["topic"]) in already
                or dedup_key(it["subject"]) in already):
            repeats += 1
            continue
        it["verdict"], it["why"] = classify(it)
''',
     'or dedup_key(it["subject"]) in already', 1),
]

RUN_EDITS = [('pool.mark_used(item["topic"])\n',
              'pool.mark_used(item["topic"], item.get("subject", ""))\n',
              'pool.mark_used(item["topic"], item.get("subject", ""))', 4)]


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
    # Compare stripped: the same statement appears at several
    # indentation levels, and an indented copy is not an unrelated line.
    replaced = {l.strip() for old, _, _, _ in edits for l in old.splitlines()}
    lost = {l.strip() for l in set(original.splitlines())
            - set(src.splitlines()) if l.strip()} - replaced
    if lost:
        return f"  {path}: would drop {len(lost)} unrelated line(s) -- NOT WRITTEN"
    path.write_text(src)
    return f"  {path}: patched {applied} place(s)"


def backfill(root: pathlib.Path) -> str:
    """Add every subject already made to used.json."""
    sys.path.insert(0, str(root.resolve()))
    import config
    from core.pool import dedup_key

    try:
        used = set(json.loads(config.USED_FILE.read_text()))
    except (OSError, json.JSONDecodeError):
        used = set()
    before = len(used)

    added = []
    for f in sorted((root / "state/pending").glob("*.json")):
        try:
            m = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for field in ("subject", "topic"):
            key = dedup_key(str(m.get(field) or ""))
            if key and key not in used:
                used.add(key)
                added.append(f"{m.get(field)} -> {key}")
    config.USED_FILE.write_text(json.dumps(sorted(used), indent=0))
    out = [f"  used.json: {before} -> {len(used)} keys"]
    for a in added:
        out.append(f"      + {a}")
    return "\n".join(out)


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/pool.py").exists() or not (root / "core/gate.py").exists():
        sys.exit("shorts repo only (~/singlescape/isnotwhatyouthink)")
    print("recording used topics under every name they can return under:")
    print(apply(root / "core/pool.py", POOL_EDITS))
    print(apply(root / "run.py", RUN_EDITS))
    print(backfill(root))


if __name__ == "__main__":
    main()
