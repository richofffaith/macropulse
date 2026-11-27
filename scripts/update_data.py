#!/usr/bin/env python3
"""Write the saved files the site reads.

The dashboard only opens those files. Run this on a schedule (or by hand)
so visitors never wait on FRED or the state layoff sites.

    python scripts/update_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from company_reports import refresh_company_reports
from run_all import run_all


def main() -> None:
    run_all()
    try:
        refresh_company_reports()
    except Exception as exc:
        print(f"Company reports were not refreshed: {exc}")


if __name__ == "__main__":
    main()
