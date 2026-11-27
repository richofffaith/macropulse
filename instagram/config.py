"""Instagram pipeline config.

Phase C schedule (America/Los_Angeles) — not wired to a cron yet:
  Mon–Fri 11:00
  Sat–Sun 12:15
Always require human approval before any non-dry-run publish.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent

# Output artifacts for each run
OUTPUT_DIR = REPO_ROOT / "output" / "instagram"
VIDEO_FILENAME = "reel.mp4"
PREVIEW_FILENAME = "preview_frame.png"
CAPTION_FILENAME = "caption.txt"
PUBLISH_LOG_FILENAME = "publish_dry_run.log"
SCENE_BRIEF_FILENAME = "scene_brief.txt"
APPROVED_MARKER = "APPROVED"  # drop this file in OUTPUT_DIR (or pass --approve)

# Default locked creative (soft BIP desk). Identity photos are NOT in git —
# pass absolute Project-store paths via --identity.
DEFAULT_SCENE_PROMPT = (
    "Founder at dual-monitor desk in a highrise at night; city timelapse window; "
    "casual tech desk outfit (not gym). Left screen: Cursor chatting; right screen: "
    "MacroPulse slowly being built. Soft brand, audience-first. Subject ~6'1\" lean "
    "athletic, dark curly hair volume on top, light goatee. 9:16 Reel."
)

# Vertical Reel
WIDTH = 1080
HEIGHT = 1920
FPS = 24
DURATION_SECONDS = 12  # keep short for dry-run proof; ≤60s for production

# Soft brand colors matching MacroPulse dashboard
BG = (7, 9, 14)
ACCENT = (59, 91, 255)
TEAL = (16, 185, 129)
TEXT = (248, 250, 252)
MUTED = (148, 163, 184)

# Phase C posting window (document for scheduler; unused until Phase C)
TIMEZONE = ZoneInfo("America/Los_Angeles")
WEEKDAY_POST_TIME = (11, 0)  # Mon–Fri 11:00 PT
WEEKEND_POST_TIME = (12, 15)  # Sat–Sun 12:15 PT

# Placeholder note when offline fallbacks are used (Higgsfield creds missing).
PLACEHOLDER_FOOTAGE_NOTE = (
    "Offline/fallback visuals (placeholder compositor or local keyframes). "
    "Locked provider: Higgsfield Seedance 2.5 R2V with identity refs + desk env. "
    "Set HF_API_KEY_ID + HF_API_KEY_SECRET (or HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET) "
    "for live generation. Identity photos stay outside git (Project store)."
)

# Locked video stack
VIDEO_PROVIDER_LOCKED = "higgsfield"
SEEDANCE_ENGINE = "bytedance/seedance-2.5/reference-to-video"
KLING_FACE_ENGINE = "kling-video/v3.0/pro/image-to-video"
