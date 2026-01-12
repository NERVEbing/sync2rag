from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from .config import AppConfig
from .utils import ensure_dir


def clear_state(config: AppConfig) -> dict[str, Any]:
    logger = logging.getLogger(__name__)
    state_dir = config.runtime.state_dir
    removed: list[str] = []

    removed.extend(_clear_dir(state_dir, logger, recreate=True))
    logger.info("State cleared: %d entries removed", len(removed))
    return {"state_dir": str(state_dir), "removed": removed}


def clear_all(config: AppConfig) -> dict[str, Any]:
    logger = logging.getLogger(__name__)
    removed: list[str] = []

    removed.extend(_clear_dir(config.runtime.state_dir, logger, recreate=True))
    removed.extend(_clear_dir(config.output.root_dir, logger, recreate=True))
    removed.extend(_clear_dir(config.manifest.full_path.parent, logger, recreate=True))

    logger.info("All cleared: %d entries removed", len(removed))
    return {
        "state_dir": str(config.runtime.state_dir),
        "output_dir": str(config.output.root_dir),
        "manifest_dir": str(config.manifest.full_path.parent),
        "removed": removed,
    }


def _clear_dir(path: Path, logger: logging.Logger, recreate: bool) -> list[str]:
    removed: list[str] = []
    if not path.exists():
        logger.info("Directory not found: %s", path)
        return removed

    for entry in list(path.iterdir()):
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed.append(str(entry))
        except Exception as exc:
            logger.warning("Failed to remove %s: %s", entry, exc)

    if recreate:
        ensure_dir(path)
    return removed
