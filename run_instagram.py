#!/usr/bin/env python3
"""CLI: MacroPulse Instagram Reel dry-run pipeline.

Default is --dry-run (render + caption + mocked publish log). Never live-posts
without explicit non-dry-run + human approval + Meta credentials.

Phase C schedule (config only for now), America/Los_Angeles:
  Mon–Fri 11:00  |  Sat–Sun 12:15
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from instagram.caption import generate_caption
from instagram.config import (
    CAPTION_FILENAME,
    DURATION_SECONDS,
    OUTPUT_DIR,
    PLACEHOLDER_FOOTAGE_NOTE,
    TIMEZONE,
    WEEKDAY_POST_TIME,
    WEEKEND_POST_TIME,
)
from instagram.publish import dry_run_publish, live_publish_stub
from instagram.render import render_reel


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="MacroPulse Instagram Reel pipeline (dry-run by default).",
    )
    p.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Render + caption + mocked publish log (default: true). Use --no-dry-run for live stub.",
    )
    p.add_argument(
        "--approve",
        action="store_true",
        help="Human approval for non-dry-run publish (or drop APPROVED file in output dir).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Artifact directory (default: {OUTPUT_DIR})",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=DURATION_SECONDS,
        help=f"Reel length in seconds (default: {DURATION_SECONDS}, max 60).",
    )
    p.add_argument(
        "--caption-seed",
        type=int,
        default=None,
        help="Stable caption template index seed.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    duration = min(max(args.duration, 1.0), 60.0)
    out: Path = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    print("MacroPulse Instagram pipeline")
    print(f"  aesthetic: founder desk / city timelapse / MacroPulse on screen")
    print(f"  footage:   PLACEHOLDER — {PLACEHOLDER_FOOTAGE_NOTE}")
    print(
        f"  schedule:  Phase C → weekday {WEEKDAY_POST_TIME[0]:02d}:{WEEKDAY_POST_TIME[1]:02d} "
        f"/ weekend {WEEKEND_POST_TIME[0]:02d}:{WEEKEND_POST_TIME[1]:02d} ({TIMEZONE.key})"
    )
    print(f"  mode:      {'dry-run' if args.dry_run else 'live-stub (approval required)'}")
    print()

    print("Rendering Reel…")
    video_path = render_reel(output_dir=out, duration=duration)
    print(f"  video → {video_path}")

    caption = generate_caption(seed=args.caption_seed)
    caption_path = out / CAPTION_FILENAME
    caption_path.write_text(caption, encoding="utf-8")
    print(f"  caption → {caption_path}")

    if args.dry_run:
        log_path = dry_run_publish(video_path=video_path, caption=caption, output_dir=out)
        print(f"  publish log → {log_path}")
        print("\nDry-run complete. No Meta API calls were made.")
        return 0

    try:
        log_path = live_publish_stub(
            video_path=video_path,
            caption=caption,
            output_dir=out,
            approve=args.approve,
        )
    except (PermissionError, NotImplementedError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"  publish log → {log_path}")
    print("Live stub finished (Graph publish not wired until Meta credentials exist).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
