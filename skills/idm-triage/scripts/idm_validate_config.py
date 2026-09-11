#!/usr/bin/env python3
"""Validate an IDM Triage configuration without contacting providers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))

from config import ConfigError, load_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an IDM Triage YAML configuration")
    parser.add_argument(
        "-c", "--config",
        help="YAML configuration path; defaults to the bundled configuration",
    )
    args = parser.parse_args()

    try:
        load_config(args.config)
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2

    path = args.config or "skills/idm-triage/assets/config.yaml"
    print(f"Configuration is valid: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
