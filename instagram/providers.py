"""Video / photoreal generation providers.

LOCKED (2026-11-26): Higgsfield API
  - Primary: Seedance 2.5 reference-to-video (person + highrise desk refs)
  - Recurring face: Kling 3.0 Pro Elements (secondary)
  - Soul ID on identity photos (train once; store HIGGSFIELD_SOUL_ID)

Fable (Cursor agent models, e.g. Fable 5.2) is a coding agent — not a video API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class VideoProviderId(str, Enum):
    NONE = "none"
    KEYFRAMES = "keyframes"  # local MoviePy from stills
    PLACEHOLDER = "placeholder"  # Phase B Pillow desk compositor
    HIGGSFIELD = "higgsfield"  # locked primary
    REPLICATE = "replicate"  # not selected
    RUNWAY = "runway"  # not selected


@dataclass(frozen=True)
class ProviderStatus:
    selected: VideoProviderId
    locked: bool
    reason: str
    env_keys_needed: tuple[str, ...]
    credentials_present: bool = False


def resolve_provider(*, explicit: str | None = None) -> ProviderStatus:
    """Resolve generation path. Default = higgsfield (locked)."""
    raw = (
        explicit
        or os.environ.get("VIDEO_PROVIDER")
        or VideoProviderId.HIGGSFIELD.value
    ).strip().lower()
    try:
        selected = VideoProviderId(raw)
    except ValueError:
        selected = VideoProviderId.NONE

    if selected == VideoProviderId.HIGGSFIELD:
        from instagram.higgsfield import load_credentials

        creds = load_credentials()
        present = creds is not None
        return ProviderStatus(
            selected=selected,
            locked=True,
            reason=(
                "Higgsfield locked — Seedance 2.5 R2V primary; Kling 3.0 Pro Elements "
                "for recurring face; Soul ID on identity photos."
                + (
                    " Credentials present — live generation available."
                    if present
                    else " Credentials missing — stub only until HF_API_KEY_ID/SECRET "
                    "(or HIGGSFIELD_API_KEY/SECRET) are set."
                )
            ),
            env_keys_needed=_env_for(selected),
            credentials_present=present,
        )

    if selected in (VideoProviderId.REPLICATE, VideoProviderId.RUNWAY):
        return ProviderStatus(
            selected=selected,
            locked=False,
            reason=(
                f"Provider '{selected.value}' is not the locked choice. "
                "Use --provider higgsfield (default)."
            ),
            env_keys_needed=_env_for(selected),
        )

    if selected == VideoProviderId.KEYFRAMES:
        return ProviderStatus(
            selected=selected,
            locked=True,
            reason="Assemble 9:16 Reel from local keyframe stills (MoviePy). No video API.",
            env_keys_needed=(),
            credentials_present=True,
        )

    if selected == VideoProviderId.PLACEHOLDER:
        return ProviderStatus(
            selected=selected,
            locked=True,
            reason="Phase B Pillow placeholder desk/city compositor + MoviePy.",
            env_keys_needed=(),
            credentials_present=True,
        )

    return ProviderStatus(
        selected=VideoProviderId.NONE,
        locked=False,
        reason="No provider selected. Default is higgsfield.",
        env_keys_needed=(),
    )


def _env_for(provider: VideoProviderId) -> tuple[str, ...]:
    return {
        VideoProviderId.HIGGSFIELD: (
            "HF_API_KEY_ID",
            "HF_API_KEY_SECRET",
            "HIGGSFIELD_API_KEY",
            "HIGGSFIELD_API_SECRET",
            "HIGGSFIELD_SOUL_ID",
            "HIGGSFIELD_KLING_ELEMENT_ID",
        ),
        VideoProviderId.REPLICATE: ("REPLICATE_API_TOKEN",),
        VideoProviderId.RUNWAY: ("RUNWAY_API_KEY",),
    }.get(provider, ())


def describe_fable_capability() -> str:
    return (
        "Fable models in Cursor are coding agents (e.g. Fable 5.2). They do not "
        "expose a video generation API. Photoreal Reels use the locked Higgsfield "
        "stack: Seedance 2.5 R2V (primary), Kling 3.0 Pro Elements (face series), "
        "Soul ID on identity photos. Fallback without credentials: local keyframes "
        "or Phase B placeholders."
    )


def identity_path_hint() -> str:
    return (
        "Identity photos stay in the Project store (never commit to git), e.g. "
        "media/instagram/identity/*.jpg — pass absolute paths via --identity. "
        "Live Higgsfield runs upload them via the Files API (not committed)."
    )


def validate_identity_paths(paths: Sequence[str]) -> list[str]:
    """Return existing paths; warn-skip missing. Never copies into the repo."""
    from pathlib import Path

    found: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_file():
            found.append(str(p.resolve()))
    return found
