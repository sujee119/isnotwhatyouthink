#!/usr/bin/env python3
"""Make the hook recognisable, and feed the channel famous subjects.

The evidence, from the first three published shorts, rate-normalised
because they were posted hours apart:

    Katana               1000 views / 7.3h = 137/hour   thumbnail set
    La Jamais Contente    145 views / 6.5h =  22/hour   thumbnail set
    Leap second           351 views / 19.1h =  18/hour  no thumbnail

Katana and La Jamais Contente both had thumbnails and were posted an
hour apart, so the thumbnail is controlled out and the gap is 6x.

TWO CHANGES.

1. THE HOOK MUST BE A NAME PEOPLE KNOW. That middle video's title was
   "The 1899 Electric Car That Broke Every Speed Record" -- excellent --
   but its hook, which is burned into the cover and is the first thing
   on screen, read "LA JAMAIS CONTENTE IS NOT WHAT YOU THINK". The
   format only works when the viewer already holds a belief about the
   subject, and nobody holds one about a French proper noun. The gate
   now scores how recognisable the name it chose is, and rejects the
   ones nobody would know -- so the same story would have shipped as
   "THE FIRST ELECTRIC CAR IS NOT WHAT YOU THINK".

2. A SOURCE OF FAMOUS SUBJECTS. src_wikipedia deliberately scores
   famous-but-flat articles at ZERO -- its own docstring calls an
   evergreen topic a failure mode, and sets "views": 0 because rank
   MOVEMENT is the signal. That is right for a news channel and wrong
   for this one. Wikipedia's Vital Articles level 4 is a curated list of
   roughly ten thousand subjects an educated reader is expected to
   know, which is exactly what the hook needs. Cached for a month,
   sampled with a date seed so concurrent runs agree.

The new source fails independently like every other one: if Wikipedia
is unreachable the pool is simply smaller, never broken.

VERIFY BEFORE PUSHING. This machine could not reach Wikipedia, so the
network path is untested. Run `python3 run.py pool` after patching --
it prints a line per source, and you want to see `famous` with a real
count. If it says FAILED, tell me before pushing.

    python3 tools/fix_famous.py
"""
import ast
import pathlib
import sys

# (old, new, marker, expected_occurrences)
POOL_EDITS = [
    ("import json\nimport re\n",
     "import json\nimport random\nimport re\n",
     "import random", 1),

    ('WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"\n',
     '''WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

# Wikipedia's own curated list of what an educated reader is expected to
# know: roughly ten thousand subjects, which is precisely the property
# the hook needs. "People" is left out -- the gate rejects anyone not
# dead a century, so fetching them wastes gate slots.
VITAL_API = ("https://en.wikipedia.org/w/api.php?action=parse"
             "&page=Wikipedia%3AVital%20articles%2FLevel%2F4%2F{sub}"
             "&prop=links&plnamespace=0&format=json&formatversion=2")
VITAL_SUBPAGES = ("History", "Geography", "Arts", "Philosophy_and_religion",
                  "Everyday_life", "Society_and_social_sciences",
                  "Biology_and_health_sciences", "Physical_sciences",
                  "Technology", "Mathematics")
''',
     "VITAL_API", 1),

    ("def src_wikipedia(limit: int = 80) -> tuple[list[dict], str]:\n",
     '''def _famous_list() -> list[str]:
    """The vital-articles titles, cached for a month.

    The list changes by a handful of entries a year, so refetching it
    every run would be ten API calls an hour for nothing.
    """
    cache = config.STATE_DIR / "famous.json"
    try:
        d = json.loads(cache.read_text())
        age = (dt.date.today() - dt.date.fromisoformat(d["fetched"])).days
        if age < 30 and d.get("titles"):
            return d["titles"]
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        pass

    titles: list[str] = []
    for sub in VITAL_SUBPAGES:
        try:
            data = get_json(VITAL_API.format(sub=sub), timeout=20)
            titles += [l["title"] for l
                       in ((data.get("parse") or {}).get("links") or [])
                       if l.get("ns") == 0 and l.get("title")]
            time.sleep(0.3)
        except Exception:
            continue          # one dead subpage must not cost the source
    titles = sorted({t for t in titles if t and ":" not in t})
    if titles:
        try:
            cache.write_text(json.dumps(
                {"fetched": dt.date.today().isoformat(), "titles": titles}))
        except OSError:
            pass
    return titles


def src_famous(n: int = 40) -> tuple[list[dict], str]:
    """Subjects the viewer already has an opinion about.

    "X IS NOT WHAT YOU THINK" needs the viewer to hold a belief about X.
    src_wikipedia scores famous-but-flat articles at zero by design,
    which starves this format of exactly the subjects it works best on:
    Katana ran at 137 views/hour, while La Jamais Contente -- a better
    story with a name nobody knows -- ran at 22.

    Sampled rather than ranked, because every entry is famous by
    construction and there is nothing to rank on. The date seed means
    every run that day draws the same sample without shared state.
    """
    titles = _famous_list()
    if not titles:
        return [], "no vital-articles list"
    rnd = random.Random(f"{config.CHANNEL}:famous:{dt.date.today().isoformat()}")
    picked = rnd.sample(titles, min(n, len(titles)))
    out = [{"source": "famous", "topic": t.replace("_", " "), "key": t,
            "views": 0, "rank": 0, "spike": 0.0}
           for t in picked]
    return out, f"{len(picked)} sampled from {len(titles)} vital articles"


def src_wikipedia(limit: int = 80) -> tuple[list[dict], str]:
''',
     "def src_famous(", 1),

    ('''WEIGHT = {"trends": 6.0, "reddit/til": 3.5, "wikipedia": 3.0,
          "reddit/eli5": 3.0, "hn": 2.0}
''',
     '''WEIGHT = {"trends": 6.0, "famous": 4.5, "reddit/til": 3.5,
          "wikipedia": 3.0, "reddit/eli5": 3.0, "hn": 2.0}
''',
     '"famous": 4.5', 1),

    # Famous items are Wikipedia titles, so they need the same one-line
    # description -- without it classify() cannot run the person check
    # and every one of them lands in CHECK.
    ('        if it["source"] != "wikipedia":\n            continue\n',
     '        if it["source"] not in ("wikipedia", "famous"):\n            continue\n',
     'not in ("wikipedia", "famous")', 1),

    ('        wiki = [i for i in items if i["source"] == "wikipedia"]\n',
     '        wiki = [i for i in items\n'
     '                if i["source"] in ("wikipedia", "famous")]\n',
     'if i["source"] in ("wikipedia", "famous")', 2),

    ('''    try:
        got, _ = src_hn()
        items += got
        notes.append(f"hn {len(got)}")
    except Exception as exc:
        notes.append(f"hn FAILED {type(exc).__name__}")
''',
     '''    try:
        got, fnote = src_famous()
        items += got
        notes.append(f"famous {len(got)}")
    except Exception as exc:
        notes.append(f"famous FAILED {type(exc).__name__}")

    try:
        got, _ = src_hn()
        items += got
        notes.append(f"hn {len(got)}")
    except Exception as exc:
        notes.append(f"hn FAILED {type(exc).__name__}")
''',
     'notes.append(f"famous {len(got)}")', 1),

    ('''    for label, fn in (("wikipedia", lambda: src_wikipedia(wiki_limit)),
                      ("hn", src_hn)):
''',
     '''    for label, fn in (("wikipedia", lambda: src_wikipedia(wiki_limit)),
                      ("famous", src_famous),
                      ("hn", src_hn)):
''',
     '("famous", src_famous)', 1),
]

CONFIG_EDITS = [
    ('GATE_OTHER_SLOTS = int(os.getenv("GATE_OTHER_SLOTS") or 32)\n',
     '''GATE_OTHER_SLOTS = int(os.getenv("GATE_OTHER_SLOTS") or 32)
# Famous subjects get their own reserved slots. They are the lane the
# hook actually works on -- Katana ran 6x the rate of a better story
# with an unrecognisable name -- so they must not compete with trends
# for the same allocation.
GATE_FAMOUS_SLOTS = int(os.getenv("GATE_FAMOUS_SLOTS") or 16)

# Minimum share of viewers who must recognise the hook's subject line.
# "LA JAMAIS CONTENTE IS NOT WHAT YOU THINK" scores about 0.05 and is
# the reason this exists. Raise it to be stricter about fame.
HOOK_FAMILIAR_MIN = float(os.getenv("HOOK_FAMILIAR_MIN") or 0.45)
''',
     "GATE_FAMOUS_SLOTS", 1),
]

GATE_EDITS = [
    ('''- "subject": the subject as it should appear in the hook, 1-4 words, no
  parentheses, no leading article
''',
     '''- "subject": the name a general viewer RECOGNISES ON SIGHT, 1-4 words,
  no parentheses, no leading article. This line becomes the hook, and
  the hook is the whole video: "X IS NOT WHAT YOU THINK" only works if
  the viewer already holds a belief about X to overturn. Prefer the
  familiar name over the formally correct one -- "The first electric
  car", not "La Jamais Contente".
- "familiar": 0-1, the share of ordinary viewers who would recognise
  the subject line you just wrote, without looking it up. Score the
  NAME, not the topic behind it: "Katana" is 0.9, "La Jamais Contente"
  is 0.05 even though its story is excellent.
''',
     "RECOGNISES ON SIGHT", 1),

    ('''  {"n": 1, "verdict": "approve", "is_person": false,
   "subject": "Daylight saving time", "confidence": 0.9},
''',
     '''  {"n": 1, "verdict": "approve", "is_person": false,
   "subject": "Daylight saving time", "familiar": 0.95, "confidence": 0.9},
''',
     '"familiar": 0.95', 1),

    ('''        if not subject or len(subject.split()) > 6:
            it["gate"] = "reject"
            it["gate_reason"] = "approved without a usable subject"
            continue
''',
     '''        if not subject or len(subject.split()) > 6:
            it["gate"] = "reject"
            it["gate_reason"] = "approved without a usable subject"
            continue

        # A hook nobody recognises is not a hook. Defaults to 1.0 when
        # the model omits the field, so an older model degrades to the
        # old behaviour rather than emptying the pool.
        try:
            familiar = float(v.get("familiar", 1.0))
        except (TypeError, ValueError):
            familiar = 1.0
        if familiar < config.HOOK_FAMILIAR_MIN:
            it["gate"] = "reject"
            it["gate_reason"] = (f"nobody would recognise "
                                 f"'{subject}' ({familiar:.2f})")
            continue
        it["familiar"] = familiar
''',
     "nobody would recognise", 1),
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
            return (f"  {path}: expected {count} occurrence(s) of a target, "
                    f"found {n} -- NOT TOUCHED")
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


RUN_EDITS = [(
    '''    trend_slots = [i for i in ranked
                   if i["source"] == "trends"][:config.GATE_TREND_SLOTS]
    other_slots = [i for i in ranked
                   if i["source"] != "trends"][:config.GATE_OTHER_SLOTS]
    candidates = trend_slots + other_slots
    print(f"      to the gate: {len(trend_slots)} trend, "
          f"{len(other_slots)} other")
''',
    '''    famous_slots = [i for i in ranked
                    if i["source"] == "famous"][:config.GATE_FAMOUS_SLOTS]
    trend_slots = [i for i in ranked
                   if i["source"] == "trends"][:config.GATE_TREND_SLOTS]
    other_slots = [i for i in ranked
                   if i["source"] not in ("trends", "famous")
                   ][:config.GATE_OTHER_SLOTS]
    candidates = famous_slots + trend_slots + other_slots
    print(f"      to the gate: {len(famous_slots)} famous, "
          f"{len(trend_slots)} trend, {len(other_slots)} other")
''',
    "famous_slots", 1)]


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/gate.py").exists():
        sys.exit("shorts repo only (~/singlescape/isnotwhatyouthink)")
    print("recognisable hooks, and a source of famous subjects:")
    print(apply(root / "config.py", CONFIG_EDITS))
    print(apply(root / "core/pool.py", POOL_EDITS))
    print(apply(root / "core/gate.py", GATE_EDITS))
    print(apply(root / "run.py", RUN_EDITS))
    print("\n  NOW RUN:  python3 run.py pool")
    print("  You want a 'famous' line with a real count. If it says "
          "FAILED, do not push -- tell me what it said.")


if __name__ == "__main__":
    main()
