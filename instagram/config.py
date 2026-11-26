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
CAPTION_FILENAME = "caption.txt"
PUBLISH_LOG_FILENAME = "publish_dry_run.log"
APPROVED_MARKER = "APPROVED"  # drop this file in OUTPUT_DIR (or pass --approve)

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

# Placeholder note: real founder/desk/city footage should replace generated stills.
PLACEHOLDER_FOOTAGE_NOTE = (
    "Visuals are generated placeholders (desk framing + city motion + MacroPulse UI mock). "
    "Replace with real dual-monitor highrise footage when available; keep MacroPulse on-screen."
)
