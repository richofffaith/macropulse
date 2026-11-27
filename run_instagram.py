#!/usr/bin/env python3
"""CLI: MacroPulse Instagram Reel dry-run pipeline.

Default is --dry-run (render + caption + mocked publish log). Never live-posts
without explicit non-dry-run + human approval + Meta credentials.

Video provider LOCKED: Higgsfield API
  Primary: Seedance 2.5 reference-to-video
  Face series: Kling 3.0 Pro Elements
  Soul ID on identity photos

Fable agent models ≠ video API — see instagram/providers.py.

Phase C schedule (config only for now), America/Los_Angeles:
  Mon–Fri 11:00  |  Sat–Sun 12:15
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from instagram.caption import generate_caption
from instagram.config import (
    CAPTION_FILENAME,
    DEFAULT_SCENE_PROMPT,
    DURATION_SECONDS,
    OUTPUT_DIR,
    PLACEHOLDER_FOOTAGE_NOTE,
    SCENE_BRIEF_FILENAME,
    TIMEZONE,
    VIDEO_FILENAME,
    WEEKDAY_POST_TIME,
    WEEKEND_POST_TIME,
)
from instagram.higgsfield import (
    credentials_help,
    extract_video_url,
    generate_seedance_r2v,
    generate_seedance_t2v,
    load_credentials,
    stub_payload,
    upload_identity_paths,
    download_file,
)
from instagram.providers import (
    VideoProviderId,
    describe_fable_capability,
    resolve_provider,
    validate_identity_paths,
)
from instagram.publish import dry_run_publish, live_publish_stub
from instagram.render import render_from_keyframes, render_reel


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
    p.add_argument(
        "--provider",
        choices=[e.value for e in VideoProviderId],
        default=VideoProviderId.HIGGSFIELD.value,
        help="Generation path (default: higgsfield). keyframes|placeholder for offline fallbacks.",
    )
    p.add_argument(
        "--identity",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Identity reference image (repeatable). Keep files outside git "
            "(Project store). Uploaded to Higgsfield when credentials exist."
        ),
    )
    p.add_argument(
        "--image-url",
        action="append",
        default=[],
        metavar="URL",
        help="Public HTTPS image URL for Seedance R2V (repeatable). Optional if --identity can upload.",
    )
    p.add_argument(
        "--scene-prompt",
        type=str,
        default=DEFAULT_SCENE_PROMPT,
        help="Creative brief / prompt for Seedance (also written to scene_brief.txt).",
    )
    p.add_argument(
        "--keyframe",
        action="append",
        default=[],
        metavar="PATH",
        help="Local still for MoviePy assembly when --provider keyframes (or offline fallback).",
    )
    p.add_argument(
        "--train-soul-id",
        action="store_true",
        help="Also POST Soul ID training on uploaded identity URLs (costs ~$2.50; optional).",
    )
    p.add_argument(
        "--fallback",
        choices=["keyframes", "placeholder", "none"],
        default="keyframes",
        help="When Higgsfield creds missing: assemble keyframes, placeholders, or stub-only.",
    )
    return p


def _write_scene_brief(
    *,
    output_dir: Path,
    provider_status,
    identity_paths: list[str],
    scene_prompt: str,
    keyframe_paths: list[str],
    image_urls: list[str],
    higgsfield_meta: dict | None = None,
) -> Path:
    payload = {
        "scene_prompt": scene_prompt,
        "identity_paths": identity_paths,
        "identity_note": "Paths only in git logs — identity binaries must not be committed.",
        "image_urls": image_urls,
        "keyframes": keyframe_paths,
        "provider": {
            "selected": provider_status.selected.value,
            "locked": provider_status.locked,
            "reason": provider_status.reason,
            "env_keys_needed": list(provider_status.env_keys_needed),
            "credentials_present": provider_status.credentials_present,
        },
        "fable_note": describe_fable_capability(),
        "footage_note": PLACEHOLDER_FOOTAGE_NOTE,
        "higgsfield": higgsfield_meta,
    }
    path = output_dir / SCENE_BRIEF_FILENAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _render_higgsfield(
    *,
    out: Path,
    duration: float,
    scene_prompt: str,
    identity_paths: list[str],
    image_urls: list[str],
    keyframe_paths: list[str],
    train_soul_id: bool,
    fallback: str,
) -> tuple[Path | None, dict]:
    """Live Seedance when creds present; otherwise stub (+ optional local fallback)."""
    creds = load_credentials()
    if creds is None:
        meta = stub_payload(
            reason="missing_credentials",
            scene_prompt=scene_prompt,
            identity_paths=identity_paths,
            image_urls=image_urls,
        )
        stub_path = out / "higgsfield_stub.json"
        stub_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(credentials_help(), file=sys.stderr)
        print(f"  stub → {stub_path}")

        if fallback == "keyframes" and keyframe_paths:
            video = render_from_keyframes(
                [Path(p) for p in keyframe_paths],
                output_dir=out,
                duration=duration,
            )
            meta["fallback_render"] = "keyframes"
            return video, meta
        if fallback == "placeholder":
            video = render_reel(output_dir=out, duration=duration)
            meta["fallback_render"] = "placeholder"
            return video, meta
        meta["fallback_render"] = None
        return None, meta

    # Live path
    uploaded: list[str] = list(image_urls)
    if identity_paths:
        print("Uploading identity / ref stills to Higgsfield…")
        uploaded.extend(upload_identity_paths(identity_paths, creds=creds))
    # Dedupe preserve order
    seen: set[str] = set()
    unique_urls: list[str] = []
    for u in uploaded:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    soul_meta = None
    if train_soul_id and unique_urls:
        from instagram.higgsfield import create_soul_id

        print("Training Soul ID (optional one-shot)…")
        soul_meta = create_soul_id(
            name="macropulse-founder",
            image_urls=unique_urls[:20],
            creds=creds,
        )
        print(f"  soul id → {soul_meta.get('id')} status={soul_meta.get('status')}")

    print("Calling Seedance 2.5…")
    dur = max(4, min(int(round(duration)), 30))
    if unique_urls:
        result = generate_seedance_r2v(
            prompt=scene_prompt,
            image_urls=unique_urls,
            creds=creds,
            duration=dur,
        )
        engine = "seedance-2.5-reference-to-video"
    else:
        print(
            "No identity uploads / --image-url — falling back to Seedance text-to-video.",
            file=sys.stderr,
        )
        result = generate_seedance_t2v(
            prompt=scene_prompt,
            creds=creds,
            duration=dur,
        )
        engine = "seedance-2.5-text-to-video"

    video_url = extract_video_url(result)
    dest = out / VIDEO_FILENAME
    download_file(video_url, dest)
    meta = {
        "mode": "higgsfield-live",
        "engine": engine,
        "request_id": result.get("request_id"),
        "video_url": video_url,
        "image_urls": unique_urls,
        "soul_id": soul_meta,
        "kling_element_env": __import__("os").environ.get("HIGGSFIELD_KLING_ELEMENT_ID"),
    }
    (out / "higgsfield_result.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return dest, meta


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    duration = min(max(args.duration, 1.0), 60.0)
    out: Path = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    provider_status = resolve_provider(explicit=args.provider)
    identity_paths = validate_identity_paths(args.identity)
    keyframe_paths = [
        str(Path(p).expanduser().resolve())
        for p in args.keyframe
        if Path(p).expanduser().is_file()
    ]
    image_urls = list(args.image_url)

    print("MacroPulse Instagram pipeline")
    print("  aesthetic: founder desk / city timelapse / MacroPulse on screen")
    print(f"  provider:  {provider_status.selected.value} (locked={provider_status.locked})")
    print(f"  provider:  {provider_status.reason}")
    if identity_paths:
        print(f"  identity:  {len(identity_paths)} ref path(s) (not copied into git)")
    elif args.identity:
        print("  identity:  WARNING — none of the --identity paths exist on disk", file=sys.stderr)
    print(
        f"  schedule:  Phase C → weekday {WEEKDAY_POST_TIME[0]:02d}:{WEEKDAY_POST_TIME[1]:02d} "
        f"/ weekend {WEEKEND_POST_TIME[0]:02d}:{WEEKEND_POST_TIME[1]:02d} ({TIMEZONE.key})"
    )
    print(f"  mode:      {'dry-run' if args.dry_run else 'live-stub (approval required)'}")
    print()

    if not provider_status.locked:
        print(provider_status.reason, file=sys.stderr)
        return 2

    print("Rendering Reel…")
    higgs_meta = None
    video_path: Path | None

    if provider_status.selected == VideoProviderId.HIGGSFIELD:
        video_path, higgs_meta = _render_higgsfield(
            out=out,
            duration=duration,
            scene_prompt=args.scene_prompt,
            identity_paths=identity_paths,
            image_urls=image_urls,
            keyframe_paths=keyframe_paths,
            train_soul_id=args.train_soul_id,
            fallback=args.fallback,
        )
        if video_path is None:
            brief = _write_scene_brief(
                output_dir=out,
                provider_status=provider_status,
                identity_paths=identity_paths,
                scene_prompt=args.scene_prompt,
                keyframe_paths=keyframe_paths,
                image_urls=image_urls,
                higgsfield_meta=higgs_meta,
            )
            print(f"  scene brief → {brief}")
            print("Higgsfield stub only (no video). Set credentials or --fallback keyframes|placeholder.")
            return 0
    elif provider_status.selected == VideoProviderId.KEYFRAMES:
        if not keyframe_paths:
            print("Error: --provider keyframes requires one or more --keyframe PATH", file=sys.stderr)
            return 2
        video_path = render_from_keyframes(
            [Path(p) for p in keyframe_paths],
            output_dir=out,
            duration=duration,
        )
    else:
        video_path = render_reel(output_dir=out, duration=duration)

    print(f"  video → {video_path}")

    brief_path = _write_scene_brief(
        output_dir=out,
        provider_status=provider_status,
        identity_paths=identity_paths,
        scene_prompt=args.scene_prompt,
        keyframe_paths=keyframe_paths,
        image_urls=image_urls,
        higgsfield_meta=higgs_meta,
    )
    print(f"  scene brief → {brief_path}")

    caption = generate_caption(seed=args.caption_seed)
    caption_path = out / CAPTION_FILENAME
    caption_path.write_text(caption, encoding="utf-8")
    print(f"  caption → {caption_path}")

    if args.dry_run:
        log_path = dry_run_publish(video_path=video_path, caption=caption, output_dir=out)
        print(f"  publish log → {log_path}")
        print("\nDry-run complete. No Meta API calls were made.")
        if higgs_meta and higgs_meta.get("mode") == "higgsfield-stub":
            print("Higgsfield was stubbed (credentials missing).")
        elif higgs_meta and higgs_meta.get("mode") == "higgsfield-live":
            print("Higgsfield Seedance generation completed; Instagram publish still dry-run.")
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
