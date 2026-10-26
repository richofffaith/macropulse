"""Refresh CA, TX, NY, and FL WARN notices plus the FRED macro batch."""

from __future__ import annotations

import pandas as pd

from collect_ca_warn import collect_warn as collect_ca_warn
from collect_fl_warn import collect_fl_warn
from collect_macro_signals import collect_macro_signals, print_macro_summary
from collect_ny_warn import collect_ny_warn
from collect_tx_warn import collect_tx_warn
from us_warn import combine_us_warn, warn_status


def _print_state_block(title: str, frame: pd.DataFrame) -> None:
    stats = warn_status(frame)
    print(f"========== {title} ==========")
    if pd.isna(stats["latest"]):
        print("No notices loaded.")
        print("=====================================\n")
        return
    print(f"Report window: {stats['earliest'].date()} to {stats['latest'].date()}")
    print(f"Notices:       {stats['notices']:,}")
    print(f"Workers:       {stats['workers']:,}")
    print(
        f"Last 30 days:  {stats['last_30_notices']:,} notices, "
        f"{stats['last_30_workers']:,} workers"
    )
    print(
        f"Prior 30 days: {stats['prior_30_workers']:,} workers "
        f"({'accelerating' if stats['rising'] else 'not accelerating'})"
    )
    print("=====================================\n")


def print_combined_summary(us_warn: pd.DataFrame, snapshot: dict) -> None:
    print_macro_summary(snapshot)
    california = us_warn[us_warn["State"] == "California"]
    texas = us_warn[us_warn["State"] == "Texas"]
    new_york = us_warn[us_warn["State"] == "New York"]
    florida = us_warn[us_warn["State"] == "Florida"]
    _print_state_block("California WARN", california)
    _print_state_block("Texas WARN", texas)
    _print_state_block("New York WARN", new_york)
    _print_state_block("Florida WARN", florida)
    _print_state_block("Combined WARN (CA + TX + NY + FL)", us_warn)

    combined = warn_status(us_warn)
    print("========== Economic Engine ==========")
    print(
        f"Macro Stress Index: {snapshot['macro_stress_index']:.1f}%  "
        f"(0 = calm, 100 = severe)"
    )
    print(
        f"Threshold flags:    {snapshot['flags_on']} / {snapshot['flags_total']}"
    )
    print(
        "WARN trend:         "
        + ("ON — layoffs up vs prior 30 days" if combined["rising"] else "off")
    )
    print("=====================================\n")


def run_all() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    print("Refreshing California WARN notices...")
    california = collect_ca_warn()
    print("Refreshing Texas WARN notices...")
    texas = collect_tx_warn()
    print("Refreshing New York WARN notices...")
    new_york = collect_ny_warn()
    print("Refreshing Florida WARN notices...")
    florida = collect_fl_warn()
    us_warn = combine_us_warn(california, texas, new_york, florida)
    print("Refreshing FRED leading indicators...")
    history, snapshot = collect_macro_signals()
    print_combined_summary(us_warn, snapshot)
    return us_warn, history, snapshot


def main() -> None:
    run_all()


if __name__ == "__main__":
    main()
