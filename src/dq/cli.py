"""Command-line entry point: `dq <command>`. Filled in as components land."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dq", description=__doc__)
    parser.parse_args(argv)
    return 0
