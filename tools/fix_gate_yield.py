#!/usr/bin/env python3
"""Stop the shorts channel producing nothing on a day it planned to post.

The 19 Oct slot-4 run, in full:

    [2/7] pool
          wikipedia 200; hn 30; reddit 37; trends 40 topics
          to the gate: 12 trend, 8 other
    [3/7] gate
          1 approved of 20 asked
    [4/7] topic: Heraldic hatching
          ! no readable sources; next topic

Nothing was built, and the run still reported success, because rc=2 is
treated as "nothing to build today".

Two separate faults, both fixed here.

1. THE GATE IS STARVED, NOT BROKEN. It rejects nearly every trend by
   design -- trending subjects are people, deaths, sport and politics,
   which this channel may never cover. So the NON-trend slots are what
   actually fill the day, and there were only eight of them. Eight
   yielded one approval, and one approval cannot survive a single dud.
   The gate is ONE batched call, so asking about more subjects costs one
   larger call, not more calls. Other slots: 8 -> 32.

2. A FAILING TOPIC BLOCKS FOR EVER. mark_used() only runs after a
   successful build, so a subject that wins the gate and then fails wins
   it again on the very next run, and the next. "Heraldic hatching" was
   in that loop. Now a subject that genuinely cannot be used is burnt at
   once. The pool offers 300+ candidates a run, so losing one costs
   nothing -- looping on it costs the whole day.

   A transient network error does NOT burn the subject: only a real
   verdict (no sources, no angle, script refused) does.

Safe to re-run: it reports "already patched" and changes nothing.

    python3 tools/fix_gate_yield.py
"""
import ast
import pathlib
import sys

# (old, new, marker). The marker must appear ONLY after patching -- it is
# what makes a second run a no-op. It cannot be the last line of `new`:
# "continue" ends three of these and occurs all over the file, and the
# config edit keeps its own first line, so a naive check re-applied it
# and duplicated the block.

CONFIG_EDITS = [(
    """GATE_ENABLED = (os.getenv("GATE_ENABLED") or "1") not in ("0", "false", "False")
""",
    '''GATE_ENABLED = (os.getenv("GATE_ENABLED") or "1") not in ("0", "false", "False")

# How many candidates reach the gate, by source. The gate rejects nearly
# every trend by design -- trending subjects are people, deaths, sport
# and politics -- so the NON-trend slots are what actually fill the day.
# Eight of them yielded one approval out of twenty on 19 Oct; that one
# failed on sources and the channel produced nothing. The gate is a
# single batched call, so asking about more subjects costs one larger
# call, not more calls.
GATE_TREND_SLOTS = int(os.getenv("GATE_TREND_SLOTS") or 12)
GATE_OTHER_SLOTS = int(os.getenv("GATE_OTHER_SLOTS") or 32)
''',
    "GATE_TREND_SLOTS = int(")]

RUN_EDITS = [
    # 1. more non-trend candidates reach the gate
    ("""    trend_slots = [i for i in ranked if i["source"] == "trends"][:12]
    other_slots = [i for i in ranked if i["source"] != "trends"][:8]
""",
     """    trend_slots = [i for i in ranked
                   if i["source"] == "trends"][:config.GATE_TREND_SLOTS]
    other_slots = [i for i in ranked
                   if i["source"] != "trends"][:config.GATE_OTHER_SLOTS]
""",
     "config.GATE_TREND_SLOTS"),

    # 2. the count was hardcoded at 20 and would now misreport
    ("""    print(f"      {len(approved)} approved of {min(len(candidates), 20)} asked")
""",
     """    print(f"      {len(approved)} approved of {len(candidates)} asked")
""",
     "approved of {len(candidates)} asked"),

    # 3. a subject with no sources must not win the gate again tomorrow
    ("""        if not sources:
            print("      ! no readable sources; next topic")
            continue
""",
     """        if not sources:
            # Burn it. mark_used() otherwise only runs after a successful
            # build, so this subject wins the gate again on every single
            # run and blocks the day -- which is exactly what "Heraldic
            # hatching" did. The pool offers 300+ candidates a run.
            print("      ! no readable sources; next topic")
            pool.mark_used(item["topic"])
            continue
""",
     "# Burn it. mark_used() otherwise only runs"),

    # 4. a real "no angle" verdict burns it; a timeout does not
    ("""        except (anglemod.NoAngle, OSError) as exc:
            print(f"      ! no angle: {exc}")
            continue
""",
     """        except (anglemod.NoAngle, OSError) as exc:
            print(f"      ! no angle: {exc}")
            # A real verdict burns the subject; a network blip does not.
            if isinstance(exc, anglemod.NoAngle):
                pool.mark_used(item["topic"])
            continue
""",
     "if isinstance(exc, anglemod.NoAngle):"),

    # 5. and a refused script
    ("""        except (scriptmod.ScriptRefused, OSError) as exc:
            print(f"      ! {exc}")
            continue
""",
     """        except (scriptmod.ScriptRefused, OSError) as exc:
            print(f"      ! {exc}")
            # A real refusal burns the subject; a network blip does not.
            if isinstance(exc, scriptmod.ScriptRefused):
                pool.mark_used(item["topic"])
            continue
""",
     "if isinstance(exc, scriptmod.ScriptRefused):"),
]


def apply(path: pathlib.Path, edits: list) -> str:
    if not path.exists():
        return f"  {path}: not here, skipped"
    src = original = path.read_text()
    applied = already = 0

    for old, new, marker in edits:
        if marker in src:
            already += 1
            continue
        n = src.count(old)
        if n == 0:
            return (f"  {path}: expected text missing -- NOT TOUCHED "
                    f"(right repo? is fix_timeouts applied first?)")
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
        sys.exit("this one is only for the shorts repo "
                 "(~/singlescape/isnotwhatyouthink) -- no core/gate.py here")
    print("widening the gate's intake and burning dead topics:")
    print(apply(root / "config.py", CONFIG_EDITS))
    print(apply(root / "run.py", RUN_EDITS))


if __name__ == "__main__":
    main()
