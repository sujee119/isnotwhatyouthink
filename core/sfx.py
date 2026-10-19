"""The reveal sound that lands on the hook.

Synthesised with ffmpeg from pure maths rather than downloaded. That means
no licence, no attribution, nothing a rights holder can claim against the
channel later, and nothing to re-source if a free-sound site disappears.
Generated once and cached in assets/sfx/.

Three styles, set by config.SFX_STYLE:

  whoosh  noise sweep rising into a low boom -- the classic reveal
  riser   upward chirp into a bright bell -- cleaner, more premium
  drop    falling tape-stop into a muted thud -- expectation collapsing
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import config

Q = ["-v", "error", "-y"]


def _run(args: list[str]) -> bool:
    try:
        subprocess.run(args, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _lavfi(expr: str, dur: float, out: Path, after: str = "") -> bool:
    af = after or "anull"
    return _run(["ffmpeg", *Q, "-f", "lavfi", "-i",
                 f"aevalsrc='{expr}':d={dur}:s=48000",
                 "-af", af, str(out)])


def _noise(color: str, dur: float, out: Path, after: str) -> bool:
    return _run(["ffmpeg", *Q, "-f", "lavfi", "-i",
                 f"anoisesrc=color={color}:d={dur}:r=48000",
                 "-af", after, str(out)])


def _combine(a: Path, b: Path, delay_ms: int, out: Path) -> bool:
    return _run([
        "ffmpeg", *Q, "-i", str(a), "-i", str(b), "-filter_complex",
        f"[1]adelay={delay_ms}|{delay_ms}[b];"
        f"[0][b]amix=inputs=2:normalize=0,alimiter=limit=0.95",
        str(out)])


def _build(style: str, tmp: Path) -> Path | None:
    """Render one style to a wav. Returns None if ffmpeg could not."""
    a, b = tmp / "_a.wav", tmp / "_b.wav"
    out = tmp / f"reveal_{style}.wav"

    if style == "riser":
        ok = (_lavfi("0.35*sin(2*PI*(180*t+900*t*t))", 0.6, a,
                     "afade=t=in:st=0:d=0.1,afade=t=out:st=0.45:d=0.15")
              and _lavfi("0.5*sin(2*PI*880*t)+0.25*sin(2*PI*1320*t)"
                         "+0.12*sin(2*PI*2640*t)", 1.1, b,
                         "afade=t=out:st=0.02:d=1.05:curve=exp,volume=0.9")
              and _combine(a, b, 520, out))
    elif style == "drop":
        ok = (_lavfi("0.4*sin(2*PI*(1200*t-520*t*t))", 0.55, a,
                     "lowpass=f=4000,afade=t=out:st=0.4:d=0.15")
              and _noise("brown", 0.5, b,
                         "lowpass=f=180,afade=t=out:st=0.03:d=0.45:"
                         "curve=exp,volume=2.2")
              and _combine(a, b, 480, out))
    else:                                   # whoosh, the default
        ok = (_noise("white", 0.75, a,
                     "highpass=f=300,afade=t=in:st=0:d=0.55:curve=exp,"
                     "afade=t=out:st=0.55:d=0.2,volume=0.5")
              and _lavfi("sin(2*PI*(55*t))", 0.9, b,
                         "afade=t=out:st=0.05:d=0.85:curve=exp,volume=1.4")
              and _combine(a, b, 560, out))

    for t in (a, b):
        t.unlink(missing_ok=True)
    return out if ok and out.exists() and out.stat().st_size > 1000 else None


def reveal(style: str = "") -> Path | None:
    """The cached reveal sound, built on first use.

    Returns None rather than raising: a missing sound effect must never
    cost a video, exactly as a missing music bed does not.
    """
    if not config.SFX_ENABLED:
        return None
    style = (style or config.SFX_STYLE or "whoosh").lower()
    if style not in ("whoosh", "riser", "drop"):
        style = "whoosh"

    cached = config.SFX_DIR / f"reveal_{style}.wav"
    if cached.exists() and cached.stat().st_size > 1000:
        return cached

    config.SFX_DIR.mkdir(parents=True, exist_ok=True)
    built = _build(style, config.SFX_DIR)
    if built is None:
        print(f"  ! could not synthesise the '{style}' reveal; "
              f"continuing without it")
        return None
    if built != cached:
        built.replace(cached)
    print(f"  built the '{style}' reveal sound")
    return cached


def mix_over(audio: Path, out_path: Path, at: float = -1.0,
             gain: float = -1.0, style: str = "") -> Path:
    """Lay the reveal over an already-mixed track.

    A separate pass rather than another branch inside render.mix_audio:
    that filter graph works and is not worth risking for one sound. If
    anything here fails the original audio is returned untouched.
    """
    sound = reveal(style)
    if sound is None:
        return audio

    at = config.SFX_AT if at < 0 else at
    gain = config.SFX_GAIN if gain < 0 else gain
    delay = max(0, int(at * 1000))

    ok = _run([
        "ffmpeg", *Q, "-i", str(audio), "-i", str(sound),
        "-filter_complex",
        f"[1:a]adelay={delay}|{delay},volume={gain:.2f}[s];"
        f"[0:a][s]amix=inputs=2:duration=first:normalize=0[out]",
        "-map", "[out]", "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100", "-ac", "2", str(out_path),
    ])
    if not ok or not out_path.exists() or out_path.stat().st_size < 2000:
        print("  ! reveal mix failed; shipping audio without it")
        return audio
    return out_path
