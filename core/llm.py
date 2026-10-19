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
