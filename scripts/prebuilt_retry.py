#!/usr/bin/env python3
"""Bound scheduled retries of the same failed prebuild; never certify a binary."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

COOLDOWN_SECONDS = 24 * 60 * 60


def allowed(marker: Path, event: str, now: float) -> bool:
    # Pushes (including a fix) and explicit manual retries always bypass cooldown.
    if event != "schedule":
        return True
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        failed_at = value["failed_at"]
        if isinstance(failed_at, bool) or not isinstance(failed_at, (int, float)):
            return True
        age = now - failed_at
        return not (0 <= age < COOLDOWN_SECONDS)
    except (OSError, ValueError, KeyError, TypeError):
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "record"))
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    args = parser.parse_args()
    if args.command == "record":
        args.marker.parent.mkdir(parents=True, exist_ok=True)
        args.marker.write_text(json.dumps({"failed_at": time.time()}) + "\n", encoding="utf-8")
        return
    proceed = allowed(args.marker, args.event, time.time())
    output = f"allowed={str(proceed).lower()}\n"
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(output)
    print(output, end="")
    if not proceed:
        message = (
            "Prebuild deferred: this version/source already failed within 24 hours. "
            "No binary was built or certified. Inspect the previous failed run, "
            "push a fix, or use workflow_dispatch for an immediate retry."
        )
        print(f"::warning::{message}")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write(message + "\n")


if __name__ == "__main__":
    main()
