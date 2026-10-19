#!/usr/bin/env python3
"""@isnotwhatyouthink -- build and publish one short.

    python run.py pool                 today's candidates, nothing built
    python run.py plan                 today's target and posting slots
    python run.py build                build one short and upload it
    python run.py build --slot 3       honour today's slot selection
    python run.py build --dry-run      build it, do not upload
    python run.py doctor               what is configured and what is not
    python run.py list                 what has been made

Exit codes from build: 0 built, 2 nothing to build (no approved topic or
this slot is not posting today), 1 something failed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import traceback
from pathlib import Path

import config

NO_STORY = 2

PENDING = config.STATE_DIR / "pending"
PENDING.mkdir(parents=True, exist_ok=True)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    # Truncate FIRST, then strip: a trailing hyphen in a release tag was a
    # 422 on the reels pipeline and cost two finished videos.
    s = s[:44].strip("-") or "short"
    return f"{dt.date.today():%Y%m%d}-{s}"


def _save(slug: str, data: dict) -> Path:
    p = PENDING / f"{slug}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return p


def _telegram(text: str) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        from core import telegram
        telegram.send(text)
    except Exception:
        pass


# ------------------------------------------------------------------ commands

def cmd_pool(args) -> int:
    from core import pool
    pool.report()
    return 0


def cmd_plan(args) -> int:
    from core import plan
    day = dt.date.today()
    print(f"{day}  target {plan.target_for(day)} shorts  "
          f"slots {plan.slots_for(day)} of {config.SLOTS}")
    for s in range(1, config.SLOTS + 1):
        go, why = plan.should_build(s, day)
        print(f"  slot {s}: {'build' if go else 'stand down'}  {why}")
    return 0


def cmd_build(args) -> int:
    from core import gate, plan, pool, script as scriptmod, topic

    slot = int(getattr(args, "slot", 0) or 0)
    go, why = plan.should_build(slot)
    print(f"[1/7] plan: {why}")
    if not go:
        return NO_STORY

    print("[2/7] pool")
    found = pool.gather()
    print(f"      {'; '.join(found['notes'])}")
    print(f"      {len(found['short'])} shortlisted, "
          f"{len(found['check'])} to check, {found['rejected']} rejected")

    # The shortlist and the flagged items both go to the gate: SHORT means
    # it survived the regexes, not that it is safe.
    candidates = found["short"] + found["check"]
    if not candidates:
        print("      nothing to put to the gate today")
        return NO_STORY

    print("[3/7] gate")
    try:
        approved = gate.review(candidates)
    except gate.GateUnavailable as exc:
        print(f"      ! {exc}")
        print("      the gate decides what may be built, so nothing is built")
        return 1
    print(f"      {len(approved)} approved of {min(len(candidates), 20)} asked")
    if not approved:
        return NO_STORY

    for item in approved:
        print(f"[4/7] topic: {item['subject']}  ({item['hook']})")
        print(f"      angle: {item['angle'][:90]}")
        sources = topic.widen(item)
        print(f"      sources: {topic.describe(sources) or 'none'}")
        if not sources:
            print("      ! no readable sources; next topic")
            continue

        print("[5/7] script")
        try:
            sc = scriptmod.generate(item, sources)
        except scriptmod.ScriptRefused as exc:
            print(f"      ! {exc}")
            continue
        print(f"      {sc.word_count()} words, {len(sc.shots)} shots")
        print(f"      title: {sc.title}")

        slug = _slug(item["subject"])
        manifest = {
            "slug": slug,
            "subject": item["subject"],
            "hook": item["hook"],
            "angle": item["angle"],
            "is_person": item.get("is_person", False),
            "topic_source": item["source"],
            "title": sc.title,
            "description": sc.description,
            "tags": sc.tags,
            "narration": sc.narration,
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "building",
            "sources": [{"kind": s["kind"], "url": s["url"]} for s in sources],
        }
        _save(slug, manifest)

        print("[6/7] render")
        try:
            out = _render(sc, slug)
        except Exception as exc:
            manifest["status"] = "failed"
            manifest["last_error"] = f"{type(exc).__name__}: {exc}"[:400]
            _save(slug, manifest)
            print(f"      ! render failed: {exc}")
            traceback.print_exc(limit=3)
            return 1
        manifest["duration"] = _duration(out)
        manifest["status"] = "rendered"
        _save(slug, manifest)
        print(f"      {out}  ({manifest['duration']:.1f}s)")

        if getattr(args, "dry_run", False):
            print("[7/7] dry run: not uploading")
            return 0

        print("[7/7] youtube")
        rc = _upload(manifest, out)
        _save(manifest["slug"], manifest)
        if rc == 0:
            pool.mark_used(item["topic"])
            _telegram(f"{manifest['hook']}\n{manifest.get('watch_url','')}")
        return rc

    print("      no topic survived script writing")
    return NO_STORY


def _render(sc, slug: str) -> Path:
    """Same render path as the reels pipeline, which is proven -- only the
    cover text and the reveal sound differ."""
    from core import captions, render, tts, visuals
    from core.visuals import Shot

    workdir = config.OUT_DIR / slug
    workdir.mkdir(parents=True, exist_ok=True)

    narration = workdir / "narration.mp3"
    words = tts.speak(sc.narration, narration)
    total = tts.duration(words)
    print(f"      voice: {tts.last_engine}, {total:.1f}s")

    shots = [Shot(query=s["query"], cue=s.get("cue", ""))
             for s in sc.shots if s.get("query")]
    if not shots:
        shots = [Shot(query=" ".join(sc.hook.split()[:3]).lower(), cue="")]
    shots = visuals.align_shots(shots, words, total)
    shots = visuals.split_long_shots(shots)

    used: set[str] = set()
    found = sum(visuals.resolve_shot(s, workdir, i, used)
                for i, s in enumerate(shots))
    print(f"      imagery: {found}/{len(shots)} found")

    captions.build_ass(words, workdir / "captions.ass")
    sequence = render.build_sequence(shots, workdir)
    music = render.pick_music()
    print(f"      music: {music.name if music else 'none'}")

    return render.finish(
        sequence, narration, workdir / "captions.ass",
        workdir / "short.mp4",
        music=music, workdir=workdir, cover=sc.cover_text)


def _duration(path: Path) -> float:
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _upload(manifest: dict, video: Path) -> int:
    from core import youtube

    if not youtube.enabled():
        print("      youtube not configured; the file is on disk only")
        manifest["status"] = "rendered"
        return 0

    desc = manifest["description"]
    srcs = [s["url"] for s in manifest.get("sources", []) if s.get("url")]
    if srcs:
        desc += "\n\nSources:\n" + "\n".join(srcs[:5])

    try:
        vid = youtube.upload(video, manifest["title"], desc,
                             category_id=config.YT_CATEGORY_ID,
                             privacy=config.YT_PRIVACY)
    except Exception as exc:
        manifest["status"] = "render_only"
        manifest["youtube_error"] = str(exc)[:300]
        print(f"      ! upload failed: {str(exc)[:160]}")
        return 1

    manifest["youtube_id"] = vid
    manifest["watch_url"] = youtube.watch_url(vid)
    manifest["status"] = "published"
    manifest.pop("youtube_error", None)
    print(f"      -> {manifest['watch_url']}")
    return 0


def cmd_doctor(args) -> int:
    from core import plan, sfx
    rows = [
        ("channel", config.CHANNEL),
        ("today", f"{plan.target_for()} shorts, slots {plan.slots_for()}"),
        ("length", f"{config.TARGET_SECONDS}-{config.MAX_SECONDS}s narration"),
        ("script provider", config.SCRIPT_PROVIDER),
        ("gemini key", "set" if config.GEMINI_API_KEY else "MISSING"),
        ("gate", "on" if config.GATE_ENABLED else "OFF -- nothing can build"),
        ("tts chain", config.TTS_PROVIDERS),
        ("tts voice", config.GEMINI_TTS_VOICE),
        ("reveal sound", config.SFX_STYLE if config.SFX_ENABLED else "off"),
        ("music files", str(len(list(config.MUSIC_DIR.glob("*.mp3")) +
                                list(config.MUSIC_DIR.glob("*.m4a")) +
                                list(config.MUSIC_DIR.glob("*.wav"))))),
        ("pexels key", "set" if config.PEXELS_API_KEY else "missing"),
        ("youtube", "ready" if _yt_ready() else "NOT configured"),
        ("yt privacy", config.YT_PRIVACY),
        ("yt category", config.YT_CATEGORY_ID),
        ("made so far", str(len(list(PENDING.glob("*.json"))))),
    ]
    for k, v in rows:
        print(f"  {k:16} {v}")
    s = sfx.reveal()
    print(f"  {'reveal file':16} {s if s else 'could not be built'}")
    return 0


def _yt_ready() -> bool:
    return bool(config.YT_ENABLED and config.YT_CLIENT_ID
                and config.YT_CLIENT_SECRET and config.YT_REFRESH_TOKEN)


def cmd_list(args) -> int:
    rows = []
    for f in sorted(PENDING.glob("*.json")):
        try:
            m = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(m)
    if not rows:
        print("  nothing yet")
        return 0
    rows.sort(key=lambda m: m.get("built_at", ""))
    for m in rows[-25:]:
        print(f"  {m.get('built_at','')[:16]:18}"
              f"{str(m.get('duration',''))[:4]:>5}s  "
              f"{m.get('status',''):12}"
              f"{(m.get('youtube_id') or '-'):13}"
              f"{m.get('hook','')[:46]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="run.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")

    for name, fn in (("pool", cmd_pool), ("plan", cmd_plan),
                     ("build", cmd_build), ("doctor", cmd_doctor),
                     ("list", cmd_list)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name == "build":
            p.add_argument("--slot", type=int, default=0,
                           help="cron slot 1..SLOTS; 0 means build regardless")
            p.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return 1
    return args.fn(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Piping into `head` closes stdout early; that is not an error.
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
