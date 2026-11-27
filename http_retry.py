"""Retries for the public feeds the refresh script downloads.

The Streamlit page does not call these. Collectors do, so a brief 429 or
a dropped connection does not throw away a whole refresh.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_RETRY_STATUS = (429, 500, 502, 503, 504)


def mount_retries(session: requests.Session, total: int = 4, backoff: float = 0.8) -> requests.Session:
    retry = Retry(
        total=total,
        connect=total,
        read=total,
        status=total,
        backoff_factor=backoff,
        status_forcelist=_RETRY_STATUS,
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_bytes(url: str, headers: dict, timeout: float = 40, max_bytes: int = 12_000_000) -> bytes:
    """GET a public document, backing off on 429 and 5xx, and stop if it is huge."""
    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(4):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                chunks = []
                size = 0
                while True:
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > max_bytes:
                        raise ValueError("The remote file is larger than this refresh allows.")
                    chunks.append(block)
                return b"".join(chunks)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in _RETRY_STATUS or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            if isinstance(exc, ValueError) or attempt == 3:
                raise
        time.sleep(delay)
        delay *= 2
    if last_error:
        raise last_error
    raise RuntimeError("The download did not start.")
