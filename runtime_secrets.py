"""Read a secret from Streamlit secrets, then from the environment.

Streamlit Cloud uses `.streamlit/secrets.toml`. Docker, Railway, and Render
can set the same names as environment variables. A local `.env` is loaded
into the environment so either place works. Values are never logged.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
_PLACEHOLDERS = {"", "paste_your_key_here"}


def _from_streamlit(name: str) -> str:
    secrets_path = PROJECT_DIR / ".streamlit" / "secrets.toml"
    # Opening st.secrets with no file prints a warning into the app.
    # Cloud injects the file; local runs without it fall through to the environment.
    if not secrets_path.exists():
        return ""
    try:
        import streamlit as st

        raw = st.secrets.get(name, "")
    except Exception:
        return ""
    return str(raw).strip() if raw else ""


def load_secret(name: str) -> str:
    load_dotenv(PROJECT_DIR / ".env")
    streamed = _from_streamlit(name)
    if streamed not in _PLACEHOLDERS:
        return streamed
    value = os.environ.get(name, "").strip()
    if value in _PLACEHOLDERS:
        return ""
    return value
