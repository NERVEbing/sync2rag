from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .cleaner import clear_all, clear_state
from .config import ConfigError, load_config
from .scanner import compute_changes, scan_and_convert
from .syncer import sync_lightrag
from .utils import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="sync2rag")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan", help="Scan files and build manifests")
    subparsers.add_parser("changes", help="Show added/modified/removed files since last scan")
    clear_parser = subparsers.add_parser("clear", help="Clear cached state and indexes")
    clear_parser.add_argument("--all", action="store_true", help="Clear state, data, and manifests")
    sync_parser = subparsers.add_parser("sync", help="Sync markdown to LightRAG")
    sync_parser.add_argument("--manifest", help="Override manifest.rag.json path")

    run_parser = subparsers.add_parser("run", help="Scan then sync")
    run_parser.add_argument("--manifest", help="Override manifest.rag.json path")

    try:
        argv, config_path = _extract_config_arg(sys.argv[1:])
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(2)

    args = parser.parse_args(argv)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(2)

    setup_logging(config.runtime.log_level)

    try:
        if args.command == "scan":
            scan_and_convert(config)
        elif args.command == "changes":
            change_set = compute_changes(config)
            _print_changes(change_set)
        elif args.command == "clear":
            if args.all:
                clear_all(config)
            else:
                clear_state(config)
        elif args.command == "sync":
            manifest_path = Path(args.manifest) if args.manifest else None
            sync_lightrag(config, manifest_path=manifest_path)
        elif args.command == "run":
            scan_and_convert(config)
            manifest_path = Path(args.manifest) if args.manifest else None
            sync_lightrag(config, manifest_path=manifest_path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _extract_config_arg(argv: list[str]) -> tuple[list[str], str]:
    config_path = "config.yaml"
    cleaned: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--config", "-c"):
            if i + 1 >= len(argv):
                raise ValueError("--config requires a value")
            config_path = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--config="):
            config_path = arg.split("=", 1)[1]
            i += 1
            continue
        if arg.startswith("-c="):
            config_path = arg.split("=", 1)[1]
            i += 1
            continue
        cleaned.append(arg)
        i += 1
    return cleaned, config_path


def _print_changes(change_set: Any) -> None:
    if not change_set.has_state:
        print("No previous scan state found; all files are treated as added.")
    _print_change_group("Added", change_set.added)
    _print_change_group("Modified", change_set.modified)
    _print_change_group("Removed", change_set.removed)


def _print_change_group(title: str, items: list[str]) -> None:
    print(f"{title} ({len(items)}):")
    for item in items:
        print(f"  - {item}")


if __name__ == "__main__":
    main()
