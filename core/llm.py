"""The LLM call, with the model discovery and backoff that already work.

Lifted from the reels pipeline rather than rewritten: hardcoding a Gemini
model name is brittle (Google renames and retires them, and availability
differs per key, which surfaces as a 404 on a perfectly valid key), and
the 503 backoff exists because four retries fired inside 1.4 seconds and
all failed.

Both core.gate and core.script call through here.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import config

# Defined above the range this was lifted from, so it came across as an
# undefined name and every LLM call raised NameError.
UA = "isnotwhatyouthink/1.0"

def _post_json(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA, **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


_GEMINI_MODEL_CACHE: list[str] = []


def _gemini_models() -> list[str]:
    """Ask the key which models it can actually call.

    Hardcoding a model name is brittle: Google renames and retires them, and
    availability differs between keys and tiers, which shows up as a 404 on
    generateContent even though the key is perfectly valid. So we ask.
    """
    if _GEMINI_MODEL_CACHE:
        return _GEMINI_MODEL_CACHE

    candidates: list[str] = []
    if config.GEMINI_MODEL:
        candidates.append(config.GEMINI_MODEL)

    try:
        url = ("https://generativelanguage.googleapis.com/v1beta/models"
               f"?key={config.GEMINI_API_KEY}&pageSize=200")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())

        usable = []
        for m in data.get("models", []):
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            name = m["name"].removeprefix("models/")
            if any(bad in name for bad in ("vision", "embedding", "aqa", "tts",
                                           "image", "live", "thinking")):
                continue
            usable.append(name)

        # Prefer fast, cheap, recent. Flash models suit short scripts.
        def rank(n: str) -> tuple:
            return (0 if "flash" in n else 1,
                    0 if "latest" not in n else 1,
                    -len(n))

        usable.sort(key=rank)
        candidates += [u for u in usable if u not in candidates]
    except Exception as exc:
        print(f"  ! could not list Gemini models: {exc}")

    _GEMINI_MODEL_CACHE[:] = candidates
    return candidates


def _call_gemini(system: str, user: str) -> str:
    models = _gemini_models()
    if not models:
        raise RuntimeError(
            "No Gemini model available. Check GEMINI_API_KEY, or run:\n"
            "  curl -s 'https://generativelanguage.googleapis.com/v1beta/"
            "models?key=YOUR_KEY' | grep '\"name\"'")

    last = None
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
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < 3:
                        wait = 25 * attempt
                        why = ("rate-limited (429)" if exc.code == 429
                               else f"returned {exc.code}, its end not ours")
                        print(f"  ! gemini {why}, waiting {wait}s "
                              f"-- attempt {attempt} of 3")
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


def _gemini_once(model: str, system: str, user: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={config.GEMINI_API_KEY}")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"},
    }
    data = _post_json(url, payload, {})
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(system: str, user: str) -> str:
    payload = {
        "model": config.GROQ_MODEL,
        "temperature": 0.8,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        payload,
        {"Authorization": f"Bearer {config.GROQ_API_KEY}"},
    )
    return data["choices"][0]["message"]["content"]


def _call_ollama(system: str, user: str) -> str:
    payload = {
        "model": config.OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = _post_json(f"{config.OLLAMA_HOST}/api/chat", payload, {})
    return data["message"]["content"]


PROVIDERS = {"gemini": _call_gemini, "groq": _call_groq, "ollama": _call_ollama}


class AllProvidersFailed(OSError):
    """Every provider in the chain failed.

    OSError on purpose: each failure was a failed network call, and
    run.py already catches OSError around the gate, the angle step and
    the script step. So this needs no new handler -- the gate fails
    closed and the other two skip the candidate, which is the correct
    behaviour for a provider outage.
    """


def call(system: str, user: str) -> str:
    """Ask each provider in turn until one answers.

    Narration has worked this way since the beginning:
    TTS_PROVIDERS is "gemini,azure,edge,gtts,espeak" and speak() walks
    it, which is why a dead voice service has never cost a video. The
    script and the gate asked a single provider, so Gemini returning
    503 on 20 Oct stopped all three channels at once.

    Same signature as a provider function, so callers that already do
    fn(system, user) need no other change.
    """
    names = [n.strip() for n in config.SCRIPT_PROVIDERS.split(",") if n.strip()]
    problems: list[str] = []
    for name in names:
        fn = PROVIDERS.get(name)
        if fn is None:
            problems.append(f"{name}: no such provider")
            continue
        try:
            out = fn(system, user)
        except Exception as exc:
            problems.append(f"{name}: {type(exc).__name__} {str(exc)[:80]}")
            print(f"  ! {name} unavailable, trying the next provider")
            continue
        if name != names[0]:
            print(f"  ! answered by '{name}', not '{names[0]}'")
        return out
    raise AllProvidersFailed(
        "no script provider answered:\n  " + "\n  ".join(problems))
