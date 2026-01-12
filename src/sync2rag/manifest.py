from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import ensure_dir


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_manifest(data: dict[str, Any], path: Path) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_rag_manifest(full_manifest: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in full_manifest.get("items", []):
        if not item.get("canonical"):
            continue
        if item.get("conversion_status") != "success":
            continue
        rag = item.get("rag", {})
        if not rag:
            continue
        items.append(
            {
                "file_source": rag.get("file_source"),
                "md_path": item.get("md_path"),
                "md_sha256": item.get("md_sha256"),
                "md_public_url": item.get("md_public_url"),
                "source_rel_path": item.get("source_rel_path"),
            }
        )

    return {
        "version": full_manifest.get("version", 1),
        "generated_at": full_manifest.get("generated_at"),
        "root_dir": full_manifest.get("root_dir"),
        "items": items,
    }
