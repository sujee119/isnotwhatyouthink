#!/usr/bin/env python3
"""What this machine can actually run, before anything is built on it.

Every throughput number quoted for local video generation comes from an
RTX 4090 on native Linux. Two things here are probably not that: the
card, and WSL2 with an external GPU over Thunderbolt. Planning a
pipeline on someone else's benchmark is how you find out three days in
that a reel takes ninety minutes instead of forty.

So this measures rather than assumes, and prints the numbers the plan
actually depends on:

  * the card, its VRAM, and whether CUDA is reachable from WSL at all
  * host RAM, which is what model offloading spends when VRAM runs out
  * free disk, because the weights are 20-50GB before you generate
    a single frame
  * a real matmul benchmark, so there is a measured TFLOPS figure to
    scale the published 4090 timings against

Nothing is installed and nothing is downloaded. Read-only.

    python3 gpu_probe.py
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import time


def run(cmd, timeout=25):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip()
    except Exception as exc:
        return f"({type(exc).__name__})"


def head(title):
    print(f"\n{title}\n" + "-" * len(title))


print("=" * 62)
print("  LOCAL GENERATION -- HARDWARE PROBE")
print("=" * 62)

# ------------------------------------------------------------- platform
head("platform")
rel = platform.release()
is_wsl = "microsoft" in rel.lower() or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")
print(f"  python      {sys.version.split()[0]}")
print(f"  kernel      {rel}")
print(f"  WSL         {'yes' if is_wsl else 'no'}")
if is_wsl:
    print("              ! an external GPU over Thunderbolt is the shakiest")
    print("                part of this setup. If nvidia-smi below is empty,")
    print("                the eGPU is not reaching WSL and that is the")
    print("                first thing to fix -- nothing else matters until")
    print("                CUDA is visible here.")

# ------------------------------------------------------------------ gpu
head("gpu")
smi = shutil.which("nvidia-smi")
gpu_name, vram_mb = None, 0
if not smi:
    print("  nvidia-smi  NOT FOUND -- no NVIDIA driver visible from here")
else:
    q = run("nvidia-smi --query-gpu=name,memory.total,driver_version,"
            "compute_cap --format=csv,noheader")
    if "," in q:
        for line in q.splitlines():
            parts = [p.strip() for p in line.split(",")]
            gpu_name = parts[0]
            try:
                vram_mb = int(parts[1].split()[0])
            except (ValueError, IndexError):
                vram_mb = 0
            print(f"  card        {parts[0]}")
            print(f"  VRAM        {parts[1]}")
            print(f"  driver      {parts[2] if len(parts) > 2 else '?'}")
            print(f"  compute cap {parts[3] if len(parts) > 3 else '?'}")
    else:
        print(f"  nvidia-smi  {q[:200]}")

# ------------------------------------------------------------ cpu / ram
head("cpu / ram")
cores = os.cpu_count() or 0
ram_gb = 0.0
try:
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemTotal:"):
                ram_gb = int(line.split()[1]) / 1048576
                break
except OSError:
    pass
print(f"  cores       {cores}")
print(f"  RAM         {ram_gb:.1f} GB")
if ram_gb and ram_gb < 32:
    print("              ! under 32GB. Offloading a 14B video model spills")
    print("                into host RAM; this will be the next ceiling.")

# ----------------------------------------------------------------- disk
head("disk")
for path in (os.path.expanduser("~"), "/tmp"):
    try:
        u = shutil.disk_usage(path)
        print(f"  {path:<12}{u.free/2**30:6.1f} GB free of {u.total/2**30:.0f} GB")
    except OSError:
        pass
print("              weights needed: Wan 2.2 5B ~10GB, 14B GGUF ~15-20GB,")
print("              LTX-2 ~10-20GB, plus a TTS model (<1GB). Budget 60GB.")

# ---------------------------------------------------------------- torch
head("pytorch / cuda")
tf32 = None
try:
    import torch
    print(f"  torch       {torch.__version__}")
    avail = torch.cuda.is_available()
    print(f"  cuda avail  {avail}")
    if avail:
        print(f"  cuda ver    {torch.version.cuda}")
        print(f"  device      {torch.cuda.get_device_name(0)}")
        free, total = torch.cuda.mem_get_info()
        print(f"  free VRAM   {free/2**30:.1f} GB of {total/2**30:.1f} GB")

        # A measured number to scale the published 4090 timings against.
        head("benchmark (fp16 matmul)")
        torch.backends.cuda.matmul.allow_tf32 = True
        n = 4096
        a = torch.randn(n, n, device="cuda", dtype=torch.float16)
        b = torch.randn(n, n, device="cuda", dtype=torch.float16)
        for _ in range(3):                     # warm up
            a @ b
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        iters = 30
        for _ in range(iters):
            a @ b
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        tf32 = (2 * n**3 * iters) / dt / 1e12
        print(f"  fp16 matmul {tf32:.1f} TFLOPS measured")
        print(f"  RTX 4090    ~165 TFLOPS fp16 for reference")
        if tf32:
            print(f"  ratio       {tf32/165:.2f}x of a 4090")
        del a, b
        torch.cuda.empty_cache()
    else:
        print("              ! torch cannot see the GPU. On WSL this is")
        print("                usually the eGPU not being passed through,")
        print("                or a CPU-only torch build.")
except ImportError:
    print("  torch       not installed (fine -- nothing is built yet)")
except Exception as exc:
    print(f"  benchmark   failed: {type(exc).__name__}: {str(exc)[:120]}")

# ------------------------------------------------------------- toolchain
head("toolchain already present")
for tool in ("ffmpeg", "espeak-ng", "git", "docker", "ollama"):
    print(f"  {tool:<12}{shutil.which(tool) or '-'}")
for d in ("~/ComfyUI", "~/comfyui", "~/singlescape"):
    p = os.path.expanduser(d)
    if os.path.isdir(p):
        print(f"  {d:<12}present")

# ------------------------------------------------------------- the verdict
head("verdict")
if not gpu_name:
    print("  No GPU visible. Everything below is blocked until CUDA works")
    print("  in WSL -- that is the whole first task, not a detail.")
else:
    g = vram_mb / 1024
    print(f"  {gpu_name}, {g:.0f} GB VRAM")
    fits = []
    if g >= 7:
        fits.append("Wan 2.2 5B (~8GB, Apache 2.0)")
    if g >= 10:
        fits.append("LTX-2 Q4 GGUF (~10GB, free under $10M)")
    if g >= 11:
        fits.append("Wan 2.2 14B GGUF Q5 (~11-16GB, Apache 2.0)")
    if g >= 14:
        fits.append("HunyuanVideo 1.5 offloaded (~14GB, licence unchecked)")
    print("  should fit : " + ("\n               ".join(fits) if fits
                                else "nothing in the video tier"))
    print("  TTS        : Kokoro runs on CPU, no VRAM at all")
    if tf32:
        est = 4.0 * (165 / tf32)
        print(f"\n  Scaling the published 4090 figure by the measured ratio,")
        print(f"  one ~5s clip looks like ~{est:.0f} min on this card.")
        print(f"  A 13-shot reel is then ~{est*13/60:.1f} h, and seven reels")
        print(f"  a day is ~{est*13*7/60:.0f} h/day of GPU.")
        print("  That is an extrapolation from a matmul, not a render --")
        print("  treat it as an order of magnitude, and measure a real")
        print("  clip before trusting it.")

print("\n" + "=" * 62)
print("  paste this whole output back")
print("=" * 62)
