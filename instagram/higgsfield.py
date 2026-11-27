"""Higgsfield API client — Seedance 2.5 primary, Soul ID + Kling hooks.

Auth (official docs): Authorization: Key {key_id}:{key_secret}
  HF_API_KEY_ID + HF_API_KEY_SECRET
Aliases accepted (same values):
  HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET
  HIGGSFIELD_API_KEY alone as "key_id:key_secret"
  HF_CREDENTIALS as "key_id:key_secret"

Optional:
  HIGGSFIELD_SOUL_ID — existing Soul ID custom_reference_id
  HIGGSFIELD_KLING_ELEMENT_ID — Kling Elements id for recurring face (secondary)

Never invent secret values. If credentials are missing, callers must stub.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api.higgsfield.ai"

# Locked production defaults
SEEDANCE_R2V = "bytedance/seedance-2.5/reference-to-video"
SEEDANCE_I2V = "bytedance/seedance-2.5/image-to-video"
SEEDANCE_T2V = "bytedance/seedance-2.5/text-to-video"
KLING_I2V = "kling-video/v3.0/pro/image-to-video"
SOUL_CREATE = "/v1/custom-references"
UPLOAD_URL = "/files/generate-upload-url"

DEFAULT_ASPECT = "9:16"
DEFAULT_RESOLUTION = "720p"
POLL_INTERVAL_SEC = 3.0
POLL_TIMEOUT_SEC = 600.0

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass(frozen=True)
class HiggsfieldCredentials:
    key_id: str
    key_secret: str

    @property
    def present(self) -> bool:
        return bool(self.key_id and self.key_secret)

    def auth_header(self) -> str:
        return f"Key {self.key_id}:{self.key_secret}"


def load_credentials() -> HiggsfieldCredentials | None:
    """Resolve credentials from env. Returns None if incomplete (no invented values)."""
    key_id = (
        os.environ.get("HF_API_KEY_ID")
        or os.environ.get("HIGGSFIELD_API_KEY_ID")
        or ""
    ).strip()
    key_secret = (
        os.environ.get("HF_API_KEY_SECRET")
        or os.environ.get("HIGGSFIELD_API_SECRET")
        or ""
    ).strip()

    # Combined forms: id:secret
    combined = (
        os.environ.get("HF_CREDENTIALS")
        or os.environ.get("HIGGSFIELD_API_KEY")
        or ""
    ).strip()
    if combined and ":" in combined and (not key_id or not key_secret):
        left, right = combined.split(":", 1)
        key_id = key_id or left.strip()
        key_secret = key_secret or right.strip()

    # User-facing single name without colon → treat as key_id only (incomplete)
    if not key_id and combined and ":" not in combined:
        key_id = combined

    if key_id and key_secret:
        return HiggsfieldCredentials(key_id=key_id, key_secret=key_secret)
    return None


def credentials_help() -> str:
    return (
        "Higgsfield credentials missing. Set either:\n"
        "  HF_API_KEY_ID + HF_API_KEY_SECRET  (official docs), or\n"
        "  HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET, or\n"
        "  HIGGSFIELD_API_KEY / HF_CREDENTIALS as 'key_id:key_secret'\n"
        "Create keys in Higgsfield Console. Do not paste secrets into chat.\n"
        "Optional: HIGGSFIELD_SOUL_ID, HIGGSFIELD_KLING_ELEMENT_ID\n"
        "Docs: https://docs.higgsfield.ai/docs/authentication"
    )


def _http_json(
    method: str,
    url: str,
    *,
    creds: HiggsfieldCredentials,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    headers = {
        "Authorization": creds.auth_header(),
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Higgsfield HTTP {exc.code} {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Higgsfield network error {url}: {exc}") from exc


def upload_local_image(path: Path, *, creds: HiggsfieldCredentials) -> str:
    """Presign + PUT a local image; return public_url for model inputs."""
    suffix = path.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"Unsupported image type for Higgsfield upload: {path}")
    content_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if content_type == "image/jpg":
        content_type = "image/jpeg"

    meta = _http_json(
        "POST",
        f"{BASE_URL}{UPLOAD_URL}",
        creds=creds,
        body={"content_type": content_type},
    )
    upload_url = meta["upload_url"]
    public_url = meta["public_url"]
    upload_headers = dict(meta.get("upload_headers") or {})
    upload_headers.setdefault("Content-Type", content_type)

    data = path.read_bytes()
    req = Request(upload_url, data=data, headers=upload_headers, method="PUT")
    try:
        with urlopen(req, timeout=180.0) as resp:
            resp.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Higgsfield upload PUT failed: {exc.code} {detail}") from exc
    return public_url


def upload_identity_paths(
    paths: Sequence[str | Path],
    *,
    creds: HiggsfieldCredentials,
    max_images: int = 8,
) -> list[str]:
    """Upload local identity / keyframe images; skip unsupported suffixes."""
    urls: list[str] = []
    for raw in paths:
        if len(urls) >= max_images:
            break
        p = Path(raw)
        if not p.is_file():
            continue
        if p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        urls.append(upload_local_image(p, creds=creds))
    return urls


def create_soul_id(
    *,
    name: str,
    image_urls: Sequence[str],
    creds: HiggsfieldCredentials,
    model_version: str = "v2",
) -> dict[str, Any]:
    """Train Soul ID on identity photos (public URLs). Poll separately."""
    body = {
        "name": name[:100],
        "model_version": model_version,
        "input_images": [
            {"type": "image_url", "image_url": u} for u in image_urls
        ],
    }
    return _http_json("POST", f"{BASE_URL}{SOUL_CREATE}", creds=creds, body=body)


def get_soul_id(reference_id: str, *, creds: HiggsfieldCredentials) -> dict[str, Any]:
    return _http_json(
        "GET",
        f"{BASE_URL}{SOUL_CREATE}/{reference_id}",
        creds=creds,
    )


def submit_generation(
    endpoint_id: str,
    payload: dict[str, Any],
    *,
    creds: HiggsfieldCredentials,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    url = f"{BASE_URL}/{endpoint_id.lstrip('/')}"
    return _http_json(
        "POST",
        url,
        creds=creds,
        body=payload,
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    )


def poll_until_done(
    status_url: str,
    *,
    creds: HiggsfieldCredentials,
    timeout_sec: float = POLL_TIMEOUT_SEC,
    interval_sec: float = POLL_INTERVAL_SEC,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _http_json("GET", status_url, creds=creds)
        status = (last.get("status") or "").lower()
        if status in {"completed", "failed", "canceled", "cancelled"}:
            return last
        time.sleep(interval_sec)
    raise TimeoutError(f"Higgsfield poll timed out after {timeout_sec}s: {last}")


def download_file(url: str, dest: Path, *, timeout: float = 180.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)
    return dest


def build_seedance_prompt(scene_prompt: str) -> str:
    return (
        f"{scene_prompt.strip()} "
        "Photoreal vertical 9:16 Instagram Reel. Preserve the person's face, "
        "dark curly hair, and light goatee from the identity reference images. "
        "Casual desk outfit only — no gym clothes. Dual monitors: Cursor chat "
        "visible, then MacroPulse dashboard being built. Soft brand, cinematic "
        "night highrise city lights through the window. Slow natural camera drift."
    )


def generate_seedance_r2v(
    *,
    prompt: str,
    image_urls: Sequence[str],
    creds: HiggsfieldCredentials,
    duration: int = 8,
    resolution: str = DEFAULT_RESOLUTION,
    aspect_ratio: str = DEFAULT_ASPECT,
    generate_audio: bool = False,
) -> dict[str, Any]:
    """Primary path: Seedance 2.5 reference-to-video (person + desk env refs)."""
    if not image_urls:
        raise ValueError("Seedance R2V requires at least one image_url")
    duration = max(4, min(int(duration), 30))
    payload = {
        "prompt": build_seedance_prompt(prompt),
        "image_urls": list(image_urls),
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "bitrate_mode": "high",
        "generate_audio": generate_audio,
    }
    handle = submit_generation(SEEDANCE_R2V, payload, creds=creds)
    status_url = handle.get("status_url")
    if not status_url:
        raise RuntimeError(f"No status_url in Higgsfield response: {handle}")
    result = poll_until_done(status_url, creds=creds)
    if (result.get("status") or "").lower() != "completed":
        raise RuntimeError(f"Seedance R2V did not complete: {result}")
    return result


def generate_seedance_t2v(
    *,
    prompt: str,
    creds: HiggsfieldCredentials,
    duration: int = 8,
    resolution: str = DEFAULT_RESOLUTION,
    aspect_ratio: str = DEFAULT_ASPECT,
    generate_audio: bool = False,
) -> dict[str, Any]:
    """Fallback when no identity images can be uploaded yet."""
    duration = max(4, min(int(duration), 30))
    payload = {
        "prompt": build_seedance_prompt(prompt),
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "bitrate_mode": "high",
        "generate_audio": generate_audio,
    }
    handle = submit_generation(SEEDANCE_T2V, payload, creds=creds)
    status_url = handle.get("status_url")
    if not status_url:
        raise RuntimeError(f"No status_url in Higgsfield response: {handle}")
    result = poll_until_done(status_url, creds=creds)
    if (result.get("status") or "").lower() != "completed":
        raise RuntimeError(f"Seedance T2V did not complete: {result}")
    return result


def extract_video_url(result: dict[str, Any]) -> str:
    video = result.get("video") or {}
    url = video.get("url") if isinstance(video, dict) else None
    if not url:
        raise RuntimeError(f"No video.url in completed Higgsfield result: {result}")
    return url


def stub_payload(
    *,
    reason: str,
    scene_prompt: str,
    identity_paths: Sequence[str],
    image_urls: Sequence[str],
) -> dict[str, Any]:
    return {
        "mode": "higgsfield-stub",
        "reason": reason,
        "primary_engine": SEEDANCE_R2V,
        "secondary_face": KLING_I2V,
        "soul_id_endpoint": SOUL_CREATE,
        "would_call": [
            f"POST {BASE_URL}{UPLOAD_URL} (identity stills)",
            f"POST {BASE_URL}/{SEEDANCE_R2V} (person + desk refs, 9:16)",
            "GET status_url until completed",
            "optional: POST /v1/custom-references (Soul ID train once)",
            f"optional series: POST {BASE_URL}/{KLING_I2V} + Elements",
        ],
        "env_required": [
            "HF_API_KEY_ID + HF_API_KEY_SECRET (or HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET)",
        ],
        "env_optional": [
            "HIGGSFIELD_SOUL_ID",
            "HIGGSFIELD_KLING_ELEMENT_ID",
        ],
        "scene_prompt": scene_prompt,
        "identity_paths": list(identity_paths),
        "image_urls": list(image_urls),
        "soul_id_env": os.environ.get("HIGGSFIELD_SOUL_ID") or None,
        "kling_element_env": os.environ.get("HIGGSFIELD_KLING_ELEMENT_ID") or None,
        "docs": {
            "auth": "https://docs.higgsfield.ai/docs/authentication",
            "r2v": "https://docs.higgsfield.ai/docs/models/seedance-2-5/reference-to-video",
            "uploads": "https://docs.higgsfield.ai/docs/concepts/file-uploads",
            "soul_id": "https://docs.higgsfield.ai/docs/models/soul-id/create-character",
        },
    }
