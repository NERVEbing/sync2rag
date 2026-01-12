from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Iterable
from urllib.parse import quote


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def hash_file(path: Path, algo: str = "sha256", chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.new(algo)
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def copy_file(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    dst.write_bytes(src.read_bytes())


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def normalize_rel_path(value: str) -> str:
    value = value.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def is_relative_url(url: str) -> bool:
    lowered = url.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return False
    if lowered.startswith("data:") or lowered.startswith("file:"):
        return False
    if lowered.startswith("/") or lowered.startswith("#"):
        return False
    return True


def build_public_url(base_url: str, prefix: str, rel_path: str) -> str:
    base_url = base_url.rstrip("/") + "/" if base_url else ""
    prefix = prefix.strip("/")
    rel_path = rel_path.lstrip("/")
    combined = "/".join(part for part in (prefix, rel_path) if part)
    combined = quote(combined, safe="/-_.~%")
    return f"{base_url}{combined}"


def choose_canonical(paths: Iterable[str]) -> str:
    return sorted(paths, key=lambda p: (len(p), p))[0]
