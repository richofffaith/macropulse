"""Soft build-in-public captions — audience first, no hard site promo."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

# Soft, founder-voice options. Rotate lightly; never push a URL hard-sell.
_CAPTION_TEMPLATES = [
    (
        "Late night at the desk.\n"
        "City lights stretching while another piece of MacroPulse comes together.\n\n"
        "Building in public — one monitor for the product, one for the noise.\n"
        "If you're into macro signals and quiet shipping, you're in the right place.\n\n"
        "#buildinpublic #indiehacker #macroeconomics #founderlife"
    ),
    (
        "Dual screens. Highrise window. Same question every night:\n"
        "what does the economy feel like from here?\n\n"
        "Shipping MacroPulse slowly, carefully, out in the open.\n"
        "No pitch tonight — just the work.\n\n"
        "#buildinpublic #startuplife #dataviz #nightcoding"
    ),
    (
        "Timelapse outside. Progress inside.\n\n"
        "MacroPulse is still early — stress radar, layoff signals, the boring plumbing.\n"
        "Sharing the desk so the journey stays honest.\n\n"
        "#buildinpublic #solofounder #markets #codinglife"
    ),
]


def generate_caption(*, seed: int | None = None) -> str:
    """Return a soft BIP caption. Optional seed picks a stable template."""
    idx = (seed if seed is not None else int(datetime.now(tz=ZoneInfo("America/Los_Angeles")).timestamp())) % len(
        _CAPTION_TEMPLATES
    )
    return _CAPTION_TEMPLATES[idx].strip() + "\n"
