"""Instagram Graph API publish stub — dry-run by default; approval required for live.

Official Meta path only (Page-linked Professional IG). No unofficial bots.
Do not put Meta secrets in git; load from env when Phase C goes live.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from instagram.config import (
    APPROVED_MARKER,
    OUTPUT_DIR,
    PUBLISH_LOG_FILENAME,
    VIDEO_FILENAME,
)


def approval_granted(*, output_dir: Path, approve_flag: bool) -> bool:
    """Non-dry-run publish requires --approve or an APPROVED marker file."""
    if approve_flag:
        return True
    return (output_dir / APPROVED_MARKER).is_file()


def dry_run_publish(
    *,
    video_path: Path,
    caption: str,
    output_dir: Path | None = None,
) -> Path:
    """Write a mocked publish log; never calls Meta."""
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / PUBLISH_LOG_FILENAME
    payload = {
        "mode": "dry-run",
        "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
        "video": str(video_path),
        "caption_chars": len(caption),
        "caption_preview": caption[:160].replace("\n", " "),
        "graph_api": {
            "would_call": [
                "POST /{ig-user-id}/media (video_url / media_type=REELS)",
                "GET /{creation-id}?fields=status_code (poll)",
                "POST /{ig-user-id}/media_publish",
            ],
            "status": "skipped — dry-run (no Meta credentials used)",
        },
        "note": "Live publish blocked until Professional IG + Page token exist and approval is granted.",
    }
    log_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return log_path


def live_publish_stub(
    *,
    video_path: Path,
    caption: str,
    output_dir: Path | None = None,
    approve: bool = False,
) -> Path:
    """Gate for future Graph API publish. Raises unless approved; still no-op without creds."""
    out = output_dir or OUTPUT_DIR
    if not approval_granted(output_dir=out, approve_flag=approve):
        raise PermissionError(
            f"Publish blocked: pass --approve or create {out / APPROVED_MARKER}. "
            "Human approval is always required."
        )

    token = os.environ.get("IG_PAGE_ACCESS_TOKEN") or os.environ.get("META_PAGE_ACCESS_TOKEN")
    ig_user = os.environ.get("IG_USER_ID")
    log_path = out / PUBLISH_LOG_FILENAME

    if not token or not ig_user:
        payload = {
            "mode": "live-stub",
            "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
            "approved": True,
            "video": str(video_path),
            "status": "aborted — missing IG_PAGE_ACCESS_TOKEN / IG_USER_ID (expected until Meta setup)",
            "graph_api": "not called",
        }
        log_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return log_path

    # Future: implement official Graph Reels publish here. Never unofficial bots.
    raise NotImplementedError(
        "Graph API Reels publish not implemented yet. Use --dry-run until Phase C wiring."
    )


def ensure_video_exists(output_dir: Path) -> Path:
    path = output_dir / VIDEO_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"Missing rendered video: {path}")
    return path
