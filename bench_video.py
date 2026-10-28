#!/usr/bin/env python3
"""Time a real clip on this card, because the published numbers disagree.

One source has LTX doing 5s in under a minute; another says its desktop
build wants 32GB. Wan 2.2 5B is quoted at anywhere from 2 to 9 minutes
on a 4090. None of them benchmark a 16GB Blackwell card, and the matmul
ratio from the probe (0.60x of a 4090) understates fp8 work because
Blackwell accelerates it and Ada does not.

So this generates actual clips and times them.

Two things the public benchmarks all get wrong for this pipeline:

  * They time 5-second clips. The reels average ~2.5s per shot, and
    cost scales with frame count, so the real figure is roughly half.
  * They quote 720p. These are 9:16 reels where the shot sits behind
    captions and a cover overlay for part of its life.

Diffusers rather than ComfyUI on purpose: this ends up inside
visuals.py as a function call, headless, in a cron job. ComfyUI is for
building workflows by hand, which is not what this is.

    python3 bench_video.py --install        # deps only, then stop
    python3 bench_video.py                  # download + benchmark
    python3 bench_video.py --model ltx      # try LTX instead of Wan

UNTESTED BY THE AUTHOR. There is no GPU and no package index on the
machine this was written on, so treat the first run as part of the
experiment. Every step says what it is doing before it does it, and
fails with the actual exception rather than a guess.
"""
import argparse
import subprocess
import sys
import time

# Model ids change; these are the current Diffusers-format repos as far
# as I know them. If one 404s the script says so and prints where to
# look rather than pretending.
MODELS = {
    "wan": dict(
        repo="Wan-AI/Wan2.2-TI2V-5B-Diffusers",
        pipe="WanPipeline",
        note="Apache 2.0, ~8GB, the safe fit on 16GB",
        page="https://huggingface.co/Wan-AI",
    ),
    "ltx": dict(
        repo="Lightricks/LTX-Video",
        pipe="LTXPipeline",
        note="free under $10M revenue, the fast one",
        page="https://huggingface.co/Lightricks",
    ),
}

PROMPT = ("a slow push in on a laboratory bench, shallow depth of field, "
          "soft daylight, scientific glassware catching the light, "
          "photorealistic, cinematic")


def sh(cmd):
    print(f"  $ {cmd}")
    return subprocess.run(cmd, shell=True).returncode


def install():
    print("installing (a few GB of wheels, one time):")
    rc = sh(f"{sys.executable} -m pip install -q -U "
            f"diffusers transformers accelerate safetensors imageio "
            f"imageio-ffmpeg sentencepiece ftfy")
    print("  done" if rc == 0 else f"  pip exited {rc}")
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=list(MODELS), default="wan")
    ap.add_argument("--install", action="store_true",
                    help="install dependencies and stop")
    ap.add_argument("--seconds", type=float, default=2.5,
                    help="clip length to time (default 2.5, the real shot length)")
    ap.add_argument("--fps", type=int, default=16)
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--height", type=int, default=832, help="9:16")
    ap.add_argument("--steps", type=int, default=30)
    args = ap.parse_args()

    if args.install:
        sys.exit(install())

    spec = MODELS[args.model]
    print("=" * 62)
    print(f"  BENCHMARK -- {args.model}: {spec['repo']}")
    print(f"  {spec['note']}")
    print("=" * 62)

    try:
        import torch
        from diffusers.utils import export_to_video
        import diffusers
    except ImportError as exc:
        sys.exit(f"\nmissing {exc.name}. Run:  python3 {sys.argv[0]} --install")

    if not torch.cuda.is_available():
        sys.exit("\nCUDA not available -- the probe said it was. Check the GPU.")

    print(f"\n  diffusers   {diffusers.__version__}")
    print(f"  torch       {torch.__version__}")
    print(f"  device      {torch.cuda.get_device_name(0)}")

    Pipe = getattr(diffusers, spec["pipe"], None)
    if Pipe is None:
        sys.exit(f"\n{spec['pipe']} is not in diffusers {diffusers.__version__}. "
                 f"Try: pip install -U git+https://github.com/huggingface/diffusers")

    # Frame count must usually satisfy 4n+1 for these temporal VAEs.
    frames = int(args.seconds * args.fps)
    frames = max(13, frames - (frames - 1) % 4)
    print(f"\n  {args.width}x{args.height}, {frames} frames @ {args.fps}fps "
          f"= {frames/args.fps:.1f}s, {args.steps} steps")

    print(f"\nloading weights (first run downloads ~10-20GB)...")
    t0 = time.perf_counter()
    try:
        pipe = Pipe.from_pretrained(spec["repo"], torch_dtype=torch.bfloat16)
    except Exception as exc:
        print(f"\n  FAILED: {type(exc).__name__}: {str(exc)[:300]}")
        sys.exit(f"\n  If this is a 404 the repo id has moved. Current ids are "
                 f"listed at {spec['page']} -- rerun with the right one via "
                 f"--model after editing MODELS at the top.")
    load_s = time.perf_counter() - t0
    print(f"  loaded in {load_s:.0f}s")

    # Offloading keeps peak VRAM under the 16GB ceiling. It costs some
    # speed, which is itself worth measuring -- if it fits without this,
    # the numbers get better.
    pipe.enable_model_cpu_offload()
    try:
        pipe.vae.enable_tiling()
    except Exception:
        pass

    for run in (1, 2):                      # first includes compile warmup
        torch.cuda.reset_peak_memory_stats()
        t0 = time.perf_counter()
        out = pipe(prompt=PROMPT, width=args.width, height=args.height,
                   num_frames=frames, num_inference_steps=args.steps)
        dt = time.perf_counter() - t0
        peak = torch.cuda.max_memory_allocated() / 2**30
        print(f"\n  run {run}: {dt:.0f}s for {frames/args.fps:.1f}s of video "
              f"| peak VRAM {peak:.1f} GB")
        if run == 2:
            path = f"bench_{args.model}.mp4"
            try:
                export_to_video(out.frames[0], path, fps=args.fps)
                print(f"  wrote {path} -- watch it, the number means nothing "
                      f"if the picture is unusable")
            except Exception as exc:
                print(f"  (could not write mp4: {exc})")

            print("\n" + "=" * 62)
            for shots, reels in ((13, 7),):
                per_reel = dt * shots / 60
                print(f"  {dt:.0f}s/clip -> {per_reel:.0f} min per "
                      f"{shots}-shot reel -> {per_reel*reels/60:.1f} h/day "
                      f"at {reels} reels")
            print("  under ~3h/day is comfortable; over ~8h/day means")
            print("  hero-shot-only, or a smaller model, or fewer steps.")
            print("=" * 62)


if __name__ == "__main__":
    main()
