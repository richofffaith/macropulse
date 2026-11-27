"""Render a vertical 9:16 Reel from placeholders or supplied keyframe stills.

Photoreal video APIs are deferred (Higgsfield vs alternatives — not locked).
This module only does local MoviePy assembly.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from moviepy import ImageSequenceClip
from PIL import Image

from instagram.assets import build_desk_frame, generate_placeholder_city
from instagram.config import (
    DURATION_SECONDS,
    FPS,
    HEIGHT,
    OUTPUT_DIR,
    PLACEHOLDER_FOOTAGE_NOTE,
    PREVIEW_FILENAME,
    VIDEO_FILENAME,
    WIDTH,
)


def _resize_cover(im: Image.Image, w: int, h: int) -> Image.Image:
    """Scale to cover target, center-crop."""
    scale = max(w / im.width, h / im.height)
    nw, nh = int(im.width * scale), int(im.height * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return im.crop((left, top, left + w, top + h))


def _kenburns(im: Image.Image, t: float, *, zoom_end: float = 1.06) -> np.ndarray:
    """Slight zoom/pan on a still; t in [0, 1]."""
    z = 1.0 + (zoom_end - 1.0) * t
    cw, ch = max(1, int(WIDTH / z)), max(1, int(HEIGHT / z))
    x0 = int((im.width - cw) * (0.35 + 0.25 * t))
    y0 = int((im.height - ch) * (0.28 + 0.12 * (1 - t)))
    x0 = max(0, min(x0, im.width - cw))
    y0 = max(0, min(y0, im.height - ch))
    crop = im.crop((x0, y0, x0 + cw, y0 + ch)).resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    return np.asarray(crop)


def render_from_keyframes(
    keyframe_paths: list[Path],
    *,
    output_dir: Path | None = None,
    duration: float | None = None,
    fps: int = FPS,
    hold_seconds: float = 2.5,
) -> Path:
    """Assemble a short Reel from local stills (crossfade + ken-burns). No API calls."""
    if not keyframe_paths:
        raise ValueError("keyframes provider requires at least one --keyframe image")

    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    imgs = [_resize_cover(Image.open(p).convert("RGB"), WIDTH, HEIGHT) for p in keyframe_paths]
    n_per = max(1, int(hold_seconds * fps))
    cross = max(1, int(0.35 * fps))
    seq: list[np.ndarray] = []

    for i, im in enumerate(imgs):
        for j in range(n_per):
            t = j / max(n_per - 1, 1)
            seq.append(_kenburns(im, t))
        if i < len(imgs) - 1:
            nxt = imgs[i + 1]
            for j in range(cross):
                a = j / max(cross - 1, 1)
                f1 = _kenburns(im, 1.0).astype(np.float32)
                f2 = _kenburns(nxt, 0.0).astype(np.float32)
                seq.append((f1 * (1 - a) + f2 * a).astype(np.uint8))

    # Optional trim/pad to requested duration
    if duration is not None:
        target = max(1, int(duration * fps))
        if len(seq) > target:
            seq = seq[:target]
        elif len(seq) < target and seq:
            while len(seq) < target:
                seq.append(seq[-1])

    clip = ImageSequenceClip(seq, fps=fps)
    video_path = out / VIDEO_FILENAME
    clip.write_videofile(
        str(video_path),
        codec="libx264",
        audio=False,
        fps=fps,
        logger=None,
    )
    clip.close()

    # Preview still = first keyframe (resized)
    preview = out / PREVIEW_FILENAME
    imgs[0].save(preview, "PNG", optimize=True)

    note = (
        "Rendered from local keyframe stills (MoviePy ken-burns). "
        "Photoreal video provider NOT locked (Higgsfield vs alternatives — deferred). "
        "Identity photos must stay outside git (Project store / --identity paths only).\n"
    )
    (out / "FOOTAGE_NOTE.txt").write_text(note, encoding="utf-8")
    return video_path


def render_reel(
    *,
    output_dir: Path | None = None,
    duration: float = DURATION_SECONDS,
    fps: int = FPS,
) -> Path:
    """Build frames with slow city pan (timelapse feel) and write MP4 (Phase B placeholders)."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "_cache"
    city_path = generate_placeholder_city(cache)
    city = Image.open(city_path).convert("RGB")

    n_frames = max(1, int(duration * fps))
    frames: list[np.ndarray] = []
    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)
        pan = 0.08 + 0.84 * t
        opacity = 180 + int(40 * abs(math.sin(t * math.pi * 2)))
        frame = build_desk_frame(city=city, pan_x=pan, person_opacity=opacity)
        if frame.size != (WIDTH, HEIGHT):
            frame = frame.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        frames.append(np.asarray(frame))

    clip = ImageSequenceClip(frames, fps=fps)
    video_path = out / VIDEO_FILENAME
    clip.write_videofile(
        str(video_path),
        codec="libx264",
        audio=False,
        fps=fps,
        logger=None,
    )
    clip.close()

    preview = out / PREVIEW_FILENAME
    Image.fromarray(frames[len(frames) // 2]).save(preview, "PNG", optimize=True)

    note_path = out / "FOOTAGE_NOTE.txt"
    note_path.write_text(PLACEHOLDER_FOOTAGE_NOTE + "\n", encoding="utf-8")
    return video_path
