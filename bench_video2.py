#!/usr/bin/env python3
"""Wan 2.2, run the way Wan 2.2 is supposed to be run.

v1 produced 37 near-identical frames -- 59KB of mp4, which is what
inter-frame compression does to a video that does not move. Three
things were wrong with it, in order of how much they matter:

  1. THE VAE WAS IN bfloat16. Wan's own diffusers example loads
     AutoencoderKLWan in float32 and the rest of the pipeline in
     bfloat16, and the decoder degrades badly when it is not. The
     warning in the v1 log -- "config attributes {'clip_output': False}
     ... are not expected" -- was the VAE complaining on the way past.
     This is the likely cause of a static output and it is fixed here.

  2. OFF-DISTRIBUTION GEOMETRY. 480x832 at 16fps, when TI2V-5B is
     trained at 1280x704 at 24fps. Diffusion models fall apart outside
     the resolution and frame rate they were trained on. Default here
     is 704x1280 -- native, rotated to portrait for reels.

  3. NO GUIDANCE, NO NEGATIVE PROMPT. Wan wants guidance_scale around
     5.0; without a negative prompt there is nothing pushing it away
     from flat, still, washed-out output.

It also now MEASURES motion instead of asking you to squint at it: the
mean absolute difference between consecutive frames. Checked against
known cases -- identical frames score 0.00, a slow pan scores ~3.9,
random noise ~85. Under 1.0 is static, and the script says so outright.

The 31.5GB is already cached, so this does not download again.

    python3 bench_video2.py                 # native portrait, fixed VAE
    python3 bench_video2.py --no-offload    # you only used 10.7 of 16GB
    python3 bench_video2.py --vae-bf16      # reproduce the v1 bug on purpose
"""
import argparse
import sys
import time

REPO = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"

PROMPT = ("Slow cinematic push in across a laboratory bench. Shallow "
          "depth of field, soft daylight from a window, dust motes "
          "drifting in the air, glassware catching the light. "
          "Photorealistic, 35mm, steady camera movement.")

# Without this there is nothing steering it away from a flat still.
NEGATIVE = ("static, still image, frozen, motionless, no movement, "
            "blurry, low quality, low resolution, jpeg artifacts, "
            "watermark, text, oversaturated, distorted, deformed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=2.5)
    ap.add_argument("--fps", type=int, default=24, help="native for TI2V-5B")
    ap.add_argument("--width", type=int, default=704, help="native, portrait")
    ap.add_argument("--height", type=int, default=1280)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--guidance", type=float, default=5.0)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--no-offload", action="store_true",
                    help="peak was 10.7GB of 16 -- offloading may be costing "
                         "speed for nothing")
    ap.add_argument("--vae-bf16", action="store_true",
                    help="load the VAE in bfloat16, i.e. reproduce the v1 bug")
    args = ap.parse_args()

    import torch
    import numpy as np
    from diffusers import AutoencoderKLWan, WanPipeline
    from diffusers.utils import export_to_video

    frames = int(args.seconds * args.fps)
    frames = max(13, frames - (frames - 1) % 4)        # VAE wants 4n+1

    print("=" * 62)
    print(f"  {args.width}x{args.height} @ {args.fps}fps, {frames} frames "
          f"= {frames/args.fps:.2f}s")
    print(f"  {args.steps} steps, guidance {args.guidance}, "
          f"VAE {'bfloat16 (the v1 bug)' if args.vae_bf16 else 'float32'}, "
          f"offload {'off' if args.no_offload else 'on'}")
    print("=" * 62)

    print("\nloading (cached, no download)...")
    t0 = time.perf_counter()
    # THE FIX: float32 VAE, bfloat16 everything else, as Wan's own
    # example does it.
    vae = AutoencoderKLWan.from_pretrained(
        REPO, subfolder="vae",
        dtype=torch.bfloat16 if args.vae_bf16 else torch.float32)
    pipe = WanPipeline.from_pretrained(REPO, vae=vae, dtype=torch.bfloat16)
    print(f"  loaded in {time.perf_counter()-t0:.0f}s")

    if args.no_offload:
        pipe.to("cuda")
    else:
        pipe.enable_model_cpu_offload()
    try:
        pipe.vae.enable_tiling()
    except Exception:
        pass

    for run in range(1, args.runs + 1):
        torch.cuda.reset_peak_memory_stats()
        t0 = time.perf_counter()
        out = pipe(prompt=PROMPT, negative_prompt=NEGATIVE,
                   width=args.width, height=args.height,
                   num_frames=frames, num_inference_steps=args.steps,
                   guidance_scale=args.guidance)
        dt = time.perf_counter() - t0
        peak = torch.cuda.max_memory_allocated() / 2**30
        vid = out.frames[0]
        print(f"\n  run {run}: {dt:.0f}s | peak VRAM {peak:.1f} GB "
              f"| {len(vid)} frames")

    # Does it actually move? Measured, not eyeballed.
    arr = np.stack([np.asarray(f, dtype=np.float32) for f in vid])
    delta = float(np.abs(np.diff(arr, axis=0)).mean())
    spread = float(arr.std())

    path = "bench_wan2.mp4"
    export_to_video(vid, path, fps=args.fps)

    import os
    size_kb = os.path.getsize(path) / 1024
    print("\n" + "=" * 62)
    print(f"  motion     {delta:6.2f}  mean |frame-to-frame delta| per pixel")
    print(f"  detail     {spread:6.2f}  pixel std dev (a flat grey frame ~ 0)")
    print(f"  file       {size_kb:6.0f} KB   (v1 was 59 KB for 37 frames)")
    if delta < 1.0:
        print("\n  STILL MOVING NOTHING. The VAE dtype was not the cause.")
        print("  Next: --no-offload, then --steps 50, then LTX instead.")
    elif delta < 2.5:
        print("\n  Barely moving. Real but subtle -- watch it before judging.")
    else:
        print("\n  That is genuine motion. Watch it and judge the look.")
    print(f"\n  {dt:.0f}s/clip -> {dt*13/60:.0f} min per 13-shot reel "
          f"-> {dt*13*7/3600:.1f} h for 7 reels")
    print("=" * 62)


if __name__ == "__main__":
    main()
