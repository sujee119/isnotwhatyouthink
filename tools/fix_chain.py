#!/usr/bin/env python3
"""Give the script and the gate the fallback chain narration already has.

Gemini returned 503 Service Unavailable this evening and all three
channels stopped. The logs show two distinct gaps.

THE GATE DID NOT RETRY AT ALL. _call_gemini retries a 429 -- the free
tier's per-minute cap -- but ends with

    if exc.code != 404:
        raise

so a 503 raises on the first try. The shorts gate took one 503 and the
slot was gone. Science only survived longer because the reels script
has its own four-attempt loop around it.

THERE IS NO SECOND PROVIDER. config.SCRIPT_PROVIDER is a single name,
and gate.py, angle.py and script.py each do

    fn = llm.PROVIDERS.get(config.SCRIPT_PROVIDER)

while groq and ollama sit in PROVIDERS unused. Narration has never had
this problem, because TTS_PROVIDERS is a CHAIN --
"gemini,azure,edge,gtts,espeak" -- and speak() walks it until one
answers. The script and the gate ask one provider and give up.

So: 5xx now retries like 429, and llm.call() walks a chain. Because
llm.call has the same signature as a provider function, each of the
three call sites becomes one line and every existing fn(...) below
them keeps working untouched.

AllProvidersFailed subclasses OSError deliberately. Every provider in
the chain failed its network call, which is an I/O failure -- and
run.py already catches OSError at all three sites, so the gate fails
closed and the angle and script steps skip one candidate, exactly as
they do for any other network problem. No new handlers needed.

    python3 tools/fix_chain.py
"""
import ast
import pathlib
import sys

CONFIG_EDITS = [(
    'SCRIPT_PROVIDER = os.getenv("SCRIPT_PROVIDER") or "gemini"\n',
    '''SCRIPT_PROVIDER = os.getenv("SCRIPT_PROVIDER") or "gemini"
# A CHAIN, like TTS_PROVIDERS. Asking one provider meant a Gemini 503
# ended the run outright on 20 Oct; narration never had that problem
# because it has always had a chain to fall through. SCRIPT_PROVIDER
# still works and leads the chain, so nothing existing changes.
# ollama costs nothing to keep here -- on a runner it refuses the
# connection instantly, and on a machine running Ollama it just works.
SCRIPT_PROVIDERS = (os.getenv("SCRIPT_PROVIDERS")
                    or f"{SCRIPT_PROVIDER},groq,ollama")
''',
    "SCRIPT_PROVIDERS", 1)]

LLM_EDITS = [
    ('''                if exc.code == 429:
                    if attempt < 3:
                        wait = 25 * attempt
                        print(f"  ! gemini rate-limited (429), waiting "
                              f"{wait}s -- attempt {attempt} of 3")
                        time.sleep(wait)
                        continue
                    last = exc
                    break
''',
     '''                if exc.code == 429 or 500 <= exc.code < 600:
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
''',
     "its end not ours", 1),

    ('''PROVIDERS = {"gemini": _call_gemini, "groq": _call_groq, "ollama": _call_ollama}
''',
     '''PROVIDERS = {"gemini": _call_gemini, "groq": _call_groq, "ollama": _call_ollama}


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
        "no script provider answered:\\n  " + "\\n  ".join(problems))
''',
     "class AllProvidersFailed", 1),
]

GATE_EDITS = [(
    '''    provider = config.SCRIPT_PROVIDER
    fn = llm.PROVIDERS.get(provider)
    if fn is None:
        raise GateUnavailable(f"no such LLM provider: {provider}")
''',
    '''    # The whole chain, not one provider. A single Gemini 503 used to
    # end the run right here.
    fn = llm.call
''',
    "fn = llm.call", 1)]

ANGLE_EDITS = [(
    '''    fn = llm.PROVIDERS.get(config.SCRIPT_PROVIDER)
    if fn is None:
        raise NoAngle(f"no such provider: {config.SCRIPT_PROVIDER}")
''',
    '''    fn = llm.call          # the whole provider chain, not one name
''',
    "fn = llm.call", 1)]

SCRIPT_EDITS = [(
    '''    fn = llm.PROVIDERS.get(config.SCRIPT_PROVIDER)
    if fn is None:
        raise ScriptRefused(f"no such provider: {config.SCRIPT_PROVIDER}")
''',
    '''    fn = llm.call          # the whole provider chain, not one name
''',
    "fn = llm.call", 1)]


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
    replaced = {l.strip() for old, _, _, _ in edits for l in old.splitlines()}
    lost = {l.strip() for l in set(original.splitlines())
            - set(src.splitlines()) if l.strip()} - replaced
    if lost:
        return f"  {path}: would drop {len(lost)} unrelated line(s) -- NOT WRITTEN"
    path.write_text(src)
    return f"  {path}: patched {applied} place(s)"


def main() -> None:
    root = pathlib.Path(".")
    if not (root / "core/llm.py").exists():
        sys.exit("shorts repo only (~/singlescape/isnotwhatyouthink)")
    print("a provider chain for the script and the gate:")
    print(apply(root / "config.py", CONFIG_EDITS))
    print(apply(root / "core/llm.py", LLM_EDITS))
    print(apply(root / "core/gate.py", GATE_EDITS))
    print(apply(root / "core/angle.py", ANGLE_EDITS))
    print(apply(root / "core/script.py", SCRIPT_EDITS))


if __name__ == "__main__":
    main()
