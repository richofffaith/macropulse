"""Render a vertical 9:16 Reel from placeholder desk/city frames.

Replace generated stills with real founder footage later; keep MacroPulse on-screen.
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
    VIDEO_FILENAME,
    WIDTH,
)


def render_reel(
    *,
    output_dir: Path | None = None,
    duration: float = DURATION_SECONDS,
    fps: int = FPS,
) -> Path:
    """Build frames with slow city pan (timelapse feel) and write MP4."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "_cache"
    city_path = generate_placeholder_city(cache)
    city = Image.open(city_path).convert("RGB")

    n_frames = max(1, int(duration * fps))
    frames: list[np.ndarray] = []
    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)
        # Slow left→right pan + slight breathe on silhouette opacity
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

    note_path = out / "FOOTAGE_NOTE.txt"
    note_path.write_text(PLACEHOLDER_FOOTAGE_NOTE + "\n", encoding="utf-8")
    return video_path
