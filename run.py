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
    from core import angle as anglemod, gate, plan, pool
    from core import script as scriptmod, topic

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
    #
    # Ordered by SCORE, not by verdict. Concatenating short+check put every
    # trend behind every shortlisted item -- trends carry no Wikipedia
    # description so they are all CHECK -- and the gate's 20-slot window
    # filled with evergreen topics before reaching a single trend, which
    # made the trends weighting pointless.
    #
    # The window is then split deliberately: trends get the larger share
    # because they have priority, and the rest is reserved for other
    # sources so the day still fills when every trend is rejected. On the
    # observed yield that is most days.
    ranked = sorted(found["short"] + found["check"],
                    key=lambda i: -i.get("score", 0.0))
    trend_slots = [i for i in ranked if i["source"] == "trends"][:12]
    other_slots = [i for i in ranked if i["source"] != "trends"][:8]
    candidates = trend_slots + other_slots
    print(f"      to the gate: {len(trend_slots)} trend, "
          f"{len(other_slots)} other")
    if not candidates:
        print("      nothing to put to the gate today")
        return NO_STORY

    print("[3/7] gate")
    try:
        approved = gate.review(candidates)
    # OSError too: a network timeout talking to the gate IS the gate
    # being unavailable. Without it a transient blip raised past this
    # handler and ended the run instead of failing closed.
    except (gate.GateUnavailable, OSError) as exc:
        print(f"      ! {exc}")
        print("      the gate decides what may be built, so nothing is built")
        return 1
    print(f"      {len(approved)} approved of {min(len(candidates), 20)} asked")
    if not approved:
        return NO_STORY

    for item in approved:
        print(f"[4/7] topic: {item['subject']}  ({item['hook']})")
        sources = topic.widen(item)
        print(f"      sources: {topic.describe(sources) or 'none'}")
        if not sources:
            print("      ! no readable sources; next topic")
            continue

        # The angle is derived from the sources, never before them. The
        # gate approves the subject on safety; it has read nothing, so an
        # angle from it is recollection and can contradict the evidence.
        try:
            found = anglemod.derive(item["subject"], sources)
        # OSError too: a timeout on one candidate should skip that
        # candidate, not abandon every remaining one.
        except (anglemod.NoAngle, OSError) as exc:
            print(f"      ! no angle: {exc}")
            continue
        item["angle"] = found["angle"]
        item["angle_verdict"] = found["verdict"]
        print(f"      sources {found['verdict']}, "
              f"confidence {found['confidence']:.1f}")
        print(f"      angle: {found['angle'][:88]}")
        if found.get("note"):
            print(f"      note: {found['note'][:88]}")

        print("[5/7] script")
        try:
            sc = scriptmod.generate(item, sources)
        # OSError too, for the same reason as the angle step above.
        except (scriptmod.ScriptRefused, OSError) as exc:
            print(f"      ! {exc}")
            continue
        print(f"      {sc.word_count()} words, {len(sc.shots)} shots")
        print(f"      title: {sc.title}")

        slug = _slug(item["subject"])
        # Half of each week's posts carry the countdown, chosen at random
        # but balanced, so the two arms are comparable.
        show_cd, arm = plan.countdown_for(slot)
        print(f"      countdown: {'yes' if show_cd else 'no'} ({arm} arm)")
        manifest = {
            "slug": slug,
            "subject": item["subject"],
            "hook": item["hook"],
            "angle": item["angle"],
            "angle_verdict": item.get("angle_verdict", ""),
            "countdown": show_cd,
            "countdown_arm": arm,
            "angle_support": found["support"],
            "is_person": item.get("is_person", False),
            "topic_source": item["source"],
            "trending_on": item.get("trending_on", []),
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
            out, credits = _render(sc, slug, countdown=show_cd)
            manifest["credits"] = credits
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


def _render(sc, slug: str, countdown: bool = True) -> tuple[Path, str]:
    """Same render path as the reels pipeline, which is proven -- only the
    cover text and the reveal sound differ.

    Returns (video, credits). The credits are not optional: Openverse and
    Wikimedia Commons serve CC-BY and CC-BY-SA images, which require
    attribution. Using them in a monetised video without it breaches the
    licence, and an earlier version of this function silently dropped
    them.
    """
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

    credits = visuals.credits_block(shots)
    attributed = sum(1 for s in shots if s.credit)
    print(f"      attribution: {attributed} image(s) credited")

    captions.build_ass(words, workdir / "captions.ass")
    sequence = render.build_sequence(shots, workdir)
    music = render.pick_music()
    print(f"      music: {music.name if music else 'none'}")

    video = render.finish(
        sequence, narration, workdir / "captions.ass",
        workdir / "short.mp4",
        music=music, workdir=workdir, cover=sc.cover_text,
        countdown=countdown)
    return video, credits


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


def _tags_for(manifest: dict) -> list[str]:
    """Topic tags from the writer, plus whatever trend phrase matched.

    The trend match is the only part with search demand behind it, so it
    is worth carrying into the metadata even though Trends does not pick
    the topic.
    """
    pool: list[str] = []
    pool += [str(t) for t in (manifest.get("tags") or [])]
    pool += [str(p) for p in (manifest.get("trending_on") or [])]
    subject = manifest.get("subject", "")
    if subject:
        pool.append(subject)
    pool.append("shorts")

    out, seen = [], set()
    for raw in pool:
        t = re.sub(r"\s+", " ", str(raw).replace("#", "")).strip().lower()
        if len(t) < 3 or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:12]


def _hashtags(tags: list[str]) -> str:
    """Three topical hashtags plus #Shorts.

    YouTube ignores EVERY hashtag in a description once there are more
    than fifteen, and reads an irrelevant pile as spam, so this stays
    deliberately small. The first three are what show above the title.
    """
    picked = []
    for t in tags:
        if t == "shorts" or " " in t:
            continue
        picked.append("#" + re.sub(r"[^a-z0-9]", "", t))
        if len(picked) == 3:
            break
    return " ".join(picked + ["#Shorts"])


def _upload(manifest: dict, video: Path) -> int:
    from core import youtube

    if not youtube.enabled():
        print("      youtube not configured; the file is on disk only")
        manifest["status"] = "rendered"
        return 0

    tags = _tags_for(manifest)
    desc = manifest["description"]
    srcs = [s["url"] for s in manifest.get("sources", []) if s.get("url")]
    if srcs:
        desc += "\n\nSources:\n" + "\n".join(srcs[:5])
    # Attribution before the hashtags, and never trimmed away: CC-BY
    # requires it, so if anything has to be dropped for length it is the
    # source links, not the credits.
    if manifest.get("credits"):
        desc += "\n\n" + manifest["credits"]
    desc += "\n\n" + _hashtags(tags)
    if len(desc) > 4900:
        desc = desc[:4900].rsplit("\n", 1)[0] + "\n\n" + _hashtags(tags)
    manifest["yt_tags"] = tags

    try:
        vid = youtube.upload(video, manifest["title"], desc,
                             category_id=config.YT_CATEGORY_ID,
                             privacy=config.YT_PRIVACY, tags=tags)
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

    # The catchphrase frame as the grid thumbnail. Cosmetic, and it runs
    # after the video is live, so a failure is reported and nothing else.
    thumb = _cover_frame(video)
    if thumb:
        why = youtube.set_thumbnail(vid, thumb)
        if why:
            manifest["thumbnail_note"] = why[:160]
            print(f"      thumbnail not set: {why[:110]}")
        else:
            manifest["thumbnail"] = "set"
            print(f"      thumbnail: the cover frame at "
                  f"{config.COVER_THUMB_MS}ms")
    return 0


def _cover_frame(video: Path) -> Path | None:
    """Pull one frame from inside the cover hold, so the catchphrase is on
    it. Returns None if ffmpeg cannot, which is not worth failing over."""
    import subprocess

    at = max(0.05, config.COVER_THUMB_MS / 1000.0)
    if at >= config.COVER_SECONDS:
        # Past the hold the overlay has faded and the frame would be bare.
        at = max(0.05, config.COVER_SECONDS * 0.4)
    out = video.with_name("thumbnail.jpg")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}",
             "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out)],
            check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out if out.exists() and out.stat().st_size > 2000 else None


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


def cmd_ab(args) -> int:
    """The countdown experiment: who is in which arm, and their video ids.

    This reports the ASSIGNMENT, not the result. YouTube holds the
    retention numbers; paste the ids into Studio or export average view
    duration and compare the two groups.
    """
    arms: dict[str, list] = {"with": [], "without": [], "manual": [],
                             "all": [], "off": []}
    for f in sorted(PENDING.glob("*.json")):
        try:
            m = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("status") != "published":
            continue
        arm = m.get("countdown_arm") or "unassigned"
        arms.setdefault(arm, []).append(m)

    for arm in ("with", "without", "manual", "all", "off", "unassigned"):
        rows = arms.get(arm) or []
        if not rows:
            continue
        print(f"\n{arm.upper()} countdown -- {len(rows)} published")
        for m in rows:
            print(f"  {m.get('built_at','')[:16]:18}"
                  f"{str(m.get('duration',''))[:4]:>5}s  "
                  f"{(m.get('youtube_id') or '-'):13}{m.get('hook','')[:40]}")

    a, b = len(arms.get("with") or []), len(arms.get("without") or [])
    print(f"\n  with {a}   without {b}")
    if a + b < 20:
        print("  Too few to read anything yet. Around 20 per arm is the "
              "point where a LARGE difference would start to show; a small "
              "one will not be visible at this sample size whatever the "
              "numbers look like.")
    else:
        print("  Compare average view duration between the groups in "
              "YouTube Studio. Treat a gap under about 10% as noise.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="run.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")

    for name, fn in (("pool", cmd_pool), ("plan", cmd_plan),
                     ("build", cmd_build), ("doctor", cmd_doctor),
                     ("list", cmd_list), ("ab", cmd_ab)):
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
