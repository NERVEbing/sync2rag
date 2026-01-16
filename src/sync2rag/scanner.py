from __future__ import annotations

import copy
import fnmatch
import json
import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from .captioner import CaptionClient, CaptioningConfig
from .config import AppConfig
from .docling_client import DoclingClient, DoclingResult
from .manifest import build_rag_manifest, now_iso, write_manifest
from .markdown_utils import rewrite_markdown_images, rewrite_markdown_images_with_placeholders
from .normalized_markdown import normalize_markdown
from .state import load_state, save_state
from .utils import (
    build_public_url,
    choose_canonical,
    ensure_dir,
    file_size_mb,
    hash_file,
    normalize_rel_path,
    read_text,
    write_text,
)


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp"}


@dataclass
class ScanResult:
    manifest: dict[str, Any]
    rag_manifest: dict[str, Any]


@dataclass
class ImageInfo:
    image_hash: str
    local_path: Path
    public_url: str
    ext: str


@dataclass
class ChangeSet:
    added: list[str]
    modified: list[str]
    removed: list[str]
    unchanged: list[str]
    has_state: bool


class CaptioningError(RuntimeError):
    pass


def scan_and_convert(config: AppConfig) -> ScanResult:
    logger = logging.getLogger(__name__)
    start_time = time.monotonic()
    ensure_dir(config.output.root_dir)
    ensure_dir(config.runtime.state_dir)

    state_path = config.runtime.state_dir / "scan_index.json"
    has_state = state_path.exists()
    prev_state = load_state(state_path) if has_state else {}
    prev_root = prev_state.get("root_dir")
    prev_items = prev_state.get("items", {})
    if not isinstance(prev_items, dict):
        prev_items = {}
    if prev_root and prev_root != str(config.input.root_dir):
        logger.warning("Scan state root_dir mismatch; ignoring previous scan state.")
        prev_items = {}
        has_state = False

    files = _collect_source_files(config)
    changes, current_meta = _build_change_set(
        files, config.input.root_dir, prev_items, has_state
    )
    if not changes.has_state:
        logger.info("Scan state not found; treating all files as added.")
    logger.info(
        "Scan start: total=%d, added=%d, modified=%d, removed=%d, unchanged=%d",
        len(files),
        len(changes.added),
        len(changes.modified),
        len(changes.removed),
        len(changes.unchanged),
    )

    items: list[dict[str, Any]] = []
    reused = 0
    for path in files:
        rel_path = _rel_path(path, config.input.root_dir)
        meta = current_meta[rel_path]
        prev_item = prev_items.get(rel_path) if prev_items else None
        reuse = _should_reuse(prev_item, meta["size"], meta["mtime"])

        item = _base_item(rel_path, path, meta["size"], meta["mtime"])
        if prev_item:
            item.update(copy.deepcopy(prev_item))
            item["source_rel_path"] = rel_path
            item["source_abs_path"] = str(path)
            item["source_ext"] = path.suffix.lower()
            item["source_size_bytes"] = meta["size"]
            item["source_mtime"] = meta["mtime"]

        if reuse:
            reused += 1
        else:
            _reset_conversion_fields(item)
            if file_size_mb(path) > config.input.max_file_size_mb:
                item["conversion_type"] = "skipped"
                item["conversion_status"] = "skipped_too_large"
                logger.warning("Skipping large file: %s", rel_path)
                items.append(item)
                continue

        if (
            item.get("source_sha256") is None
            and item.get("conversion_status") != "skipped_too_large"
        ):
            item["source_sha256"] = hash_file(path, config.manifest.hash_algo)
        items.append(item)

    _reset_stage1(items)
    _apply_stage1_dedupe(items, config)
    _ensure_canonical_ready(items)

    docling_client = DoclingClient(config.docling)
    captioner = _init_captioner(config)
    caption_state_path = config.runtime.state_dir / "vlm_caption_cache.json"
    caption_state = _load_caption_cache(caption_state_path, captioner)
    total_to_process = sum(
        1
        for item in items
        if item["conversion_status"] is None and item["stage1_canonical"]
    )
    processed_idx = 0
    try:
        for item in items:
            if item["conversion_status"] is not None:
                continue
            if not item["stage1_canonical"]:
                continue
            processed_idx += 1
            source_path = Path(item["source_abs_path"])
            ext = item["source_ext"]
            rel_path = item["source_rel_path"]
            conversion_type = (
                "passthrough" if ext in config.input.passthrough_ext else "docling"
            )
            logger.info(
                "FILE [%d/%d] type=%s file=%s",
                processed_idx,
                total_to_process,
                conversion_type,
                rel_path,
            )
            item_start = time.monotonic()
            if ext in config.input.passthrough_ext:
                _handle_passthrough(item, source_path, config)
            else:
                _handle_docling(
                    item,
                    source_path,
                    config,
                    docling_client,
                    captioner,
                    caption_state,
                )
            elapsed = time.monotonic() - item_start
            status = item.get("conversion_status") or "unknown"
            level = logging.INFO if status == "success" else logging.WARNING
            logger.log(
                level,
                "DONE [%d/%d] type=%s status=%s elapsed=%s file=%s",
                processed_idx,
                total_to_process,
                conversion_type,
                status,
                _format_duration(elapsed),
                rel_path,
            )
    finally:
        docling_client.close()
        if captioner:
            captioner.close()
        _save_caption_cache(caption_state_path, caption_state, captioner)

    _reset_stage2(items)
    _apply_stage2_dedupe(items, config)
    _assign_rag_metadata(items, config)

    manifest = {
        "version": 1,
        "generated_at": now_iso(),
        "root_dir": str(config.input.root_dir),
        "output_root": str(config.output.root_dir),
        "items": items,
    }
    rag_manifest = build_rag_manifest(manifest)

    write_manifest(manifest, config.manifest.full_path)
    write_manifest(rag_manifest, config.manifest.rag_path)
    _save_scan_state(state_path, config.input.root_dir, items)
    elapsed_total = time.monotonic() - start_time
    _log_scan_summary(logger, items, reused, processed_idx, elapsed_total)
    return ScanResult(manifest=manifest, rag_manifest=rag_manifest)


def compute_changes(config: AppConfig) -> ChangeSet:
    state_path = config.runtime.state_dir / "scan_index.json"
    has_state = state_path.exists()
    prev_state = load_state(state_path) if has_state else {}
    prev_root = prev_state.get("root_dir")
    prev_items = prev_state.get("items", {})
    if not isinstance(prev_items, dict):
        prev_items = {}
    if prev_root and prev_root != str(config.input.root_dir):
        prev_items = {}
        has_state = False

    files = _collect_source_files(config)
    changes, _ = _build_change_set(files, config.input.root_dir, prev_items, has_state)
    return changes


def _base_item(
    rel_path: str, path: Path, size_bytes: int, mtime: int
) -> dict[str, Any]:
    return {
        "source_rel_path": rel_path,
        "source_abs_path": str(path),
        "source_ext": path.suffix.lower(),
        "source_size_bytes": size_bytes,
        "source_mtime": mtime,
        "source_sha256": None,
        "stage1_canonical": False,
        "stage1_canonical_rel_path": None,
        "conversion_type": None,
        "conversion_status": None,
        "conversion_error": None,
        "md_path": None,
        "md_public_url": None,
        "md_sha256": None,
        "docling_json_path": None,
        "docling_zip_path": None,
        "image_count": 0,
        "image_index": [],
        "canonical": False,
        "canonical_rel_path": None,
        "rag": {},
    }


def _reset_conversion_fields(item: dict[str, Any]) -> None:
    item["conversion_type"] = None
    item["conversion_status"] = None
    item["conversion_error"] = None
    item["md_path"] = None
    item["md_public_url"] = None
    item["md_sha256"] = None
    item["docling_json_path"] = None
    item["docling_zip_path"] = None
    item["image_count"] = 0
    item["image_index"] = []
    item["rag"] = {}


def _reset_stage1(items: list[dict[str, Any]]) -> None:
    for item in items:
        item["stage1_canonical"] = False
        item["stage1_canonical_rel_path"] = None


def _reset_stage2(items: list[dict[str, Any]]) -> None:
    for item in items:
        item["canonical"] = False
        item["canonical_rel_path"] = None


def _ensure_canonical_ready(items: list[dict[str, Any]]) -> None:
    for item in items:
        if (
            item.get("stage1_canonical")
            and item.get("conversion_status") == "skipped_duplicate_source"
        ):
            _reset_conversion_fields(item)


def _should_reuse(
    prev_item: dict[str, Any] | None, size_bytes: int, mtime: int
) -> bool:
    if not prev_item:
        return False
    if (
        prev_item.get("source_size_bytes") != size_bytes
        or prev_item.get("source_mtime") != mtime
    ):
        return False
    if prev_item.get("conversion_status") != "success":
        return False
    md_path = prev_item.get("md_path")
    if not md_path or not Path(md_path).exists():
        return False
    return True


def _build_change_set(
    files: list[Path],
    root_dir: Path,
    prev_items: dict[str, Any],
    has_state: bool,
) -> tuple[ChangeSet, dict[str, dict[str, Any]]]:
    current_meta: dict[str, dict[str, Any]] = {}
    added: list[str] = []
    modified: list[str] = []
    unchanged: list[str] = []

    for path in files:
        rel_path = _rel_path(path, root_dir)
        stat = path.stat()
        size = stat.st_size
        mtime = int(stat.st_mtime)
        current_meta[rel_path] = {"path": path, "size": size, "mtime": mtime}
        prev_item = prev_items.get(rel_path) if prev_items else None
        if not prev_item:
            added.append(rel_path)
        elif (
            prev_item.get("source_size_bytes") == size
            and prev_item.get("source_mtime") == mtime
        ):
            unchanged.append(rel_path)
        else:
            modified.append(rel_path)

    removed = sorted(set(prev_items) - set(current_meta))
    return (
        ChangeSet(
            added=added,
            modified=modified,
            removed=removed,
            unchanged=unchanged,
            has_state=has_state,
        ),
        current_meta,
    )


def _save_scan_state(path: Path, root_dir: Path, items: list[dict[str, Any]]) -> None:
    state = {
        "version": 1,
        "root_dir": str(root_dir),
        "items": {item["source_rel_path"]: item for item in items},
        "updated_at": time.time(),
    }
    save_state(path, state)


def _log_scan_summary(
    logger: logging.Logger,
    items: list[dict[str, Any]],
    reused: int,
    processed: int,
    elapsed_sec: float,
) -> None:
    failed = sum(1 for item in items if item.get("conversion_status") == "failure")
    skipped_duplicates = sum(
        1
        for item in items
        if item.get("conversion_status") == "skipped_duplicate_source"
    )
    skipped_large = sum(
        1 for item in items if item.get("conversion_status") == "skipped_too_large"
    )
    logger.info(
        "Scan done: processed=%d, reused=%d, failed=%d, skipped_duplicates=%d, skipped_large=%d, elapsed=%s",
        processed,
        reused,
        failed,
        skipped_duplicates,
        skipped_large,
        _format_duration(elapsed_sec),
    )


def _format_duration(elapsed_sec: float) -> str:
    total = int(elapsed_sec + 0.5)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _collect_source_files(config: AppConfig) -> list[Path]:
    root_dir = config.input.root_dir
    include_ext = set(config.input.include_ext)
    passthrough_ext = set(config.input.passthrough_ext)
    allowed_exts = include_ext | passthrough_ext
    paths: list[Path] = []

    for current_root, _, files in os.walk(
        root_dir, followlinks=config.input.follow_symlinks
    ):
        for file_name in files:
            path = Path(current_root) / file_name
            rel_path = _rel_path(path, root_dir)
            if _is_excluded(rel_path, config.input.exclude_globs):
                continue
            if path.suffix.lower() not in allowed_exts:
                continue
            paths.append(path)
    paths.sort(key=lambda value: str(value))
    return paths


def _is_excluded(rel_path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def _apply_stage1_dedupe(items: list[dict[str, Any]], config: AppConfig) -> None:
    dedupe_key = _dedupe_field(config, "stage1", "source_sha256")
    canonical_strategy = _canonical_strategy(config)
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        value = item.get(dedupe_key)
        if value:
            groups.setdefault(str(value), []).append(item)

    for group in groups.values():
        paths = [item["source_rel_path"] for item in group]
        canonical = _choose_canonical(paths, canonical_strategy)
        for item in group:
            item["stage1_canonical_rel_path"] = canonical
            item["stage1_canonical"] = item["source_rel_path"] == canonical
            if not item["stage1_canonical"]:
                item["conversion_type"] = "skipped"
                item["conversion_status"] = "skipped_duplicate_source"


def _handle_passthrough(
    item: dict[str, Any], source_path: Path, config: AppConfig
) -> None:
    rel_path = Path(item["source_rel_path"]).with_suffix(".md")
    dest_path = config.output.markdown_dir / rel_path
    ensure_dir(dest_path.parent)

    md_text = read_text(source_path)
    image_index: list[dict[str, Any]] = []
    if config.output.rewrite_passthrough_md:
        md_text, image_index = rewrite_markdown_images(md_text, {})
        if config.manifest.include_image_index:
            item["image_index"] = image_index
        else:
            item["image_index"] = []
            image_index = []
    else:
        item["image_index"] = []

    md_text = normalize_markdown(md_text, image_index)
    write_text(dest_path, md_text)

    item["conversion_type"] = "passthrough"
    item["conversion_status"] = "success"
    item["md_path"] = str(dest_path)
    item["md_sha256"] = hash_file(dest_path, config.manifest.hash_algo)
    item["md_public_url"] = _public_url_for_path(dest_path, config)


def _handle_docling(
    item: dict[str, Any],
    source_path: Path,
    config: AppConfig,
    client: DoclingClient,
    captioner: CaptionClient | None,
    caption_state: dict[str, Any],
) -> None:
    logger = logging.getLogger(__name__)
    item["conversion_type"] = "docling"

    try:
        start_time = time.monotonic()
        result = client.convert_file(source_path)
        elapsed = time.monotonic() - start_time
        logger.info("Docling done in %.1fs: %s", elapsed, item["source_rel_path"])
    except Exception as exc:
        item["conversion_status"] = "failure"
        item["conversion_error"] = str(exc)
        logger.exception("Docling request failed: %s", item["source_rel_path"])
        return

    failure_reason = _resolve_docling_failure(result, config)
    if failure_reason:
        item["conversion_status"] = "failure"
        item["conversion_error"] = failure_reason
        logger.error("Docling conversion failed: %s", item["source_rel_path"])
        return

    try:
        md_text, json_text, image_index, docling_meta = _extract_docling_output(
            result, item, config, captioner, caption_state
        )
    except CaptioningError as exc:
        item["conversion_status"] = "failure"
        item["conversion_error"] = f"vlm_error: {exc}"
        logger.error("VLM captioning failed: %s", item["source_rel_path"])
        return
    except Exception as exc:
        item["conversion_status"] = "failure"
        item["conversion_error"] = str(exc)
        logger.exception("Docling post-processing failed: %s", item["source_rel_path"])
        return
    if md_text is None:
        item["conversion_status"] = "failure"
        item["conversion_error"] = "missing markdown content"
        return

    md_text = normalize_markdown(md_text, image_index)
    rel_path = Path(item["source_rel_path"]).with_suffix(".md")
    md_path = config.output.markdown_dir / rel_path
    write_text(md_path, md_text)
    caption_stats = docling_meta.get("caption_stats")
    if isinstance(caption_stats, dict):
        logger.info(
            "Captioning stats for %s: docling=%d, cache=%d, vlm=%d",
            item["source_rel_path"],
            int(caption_stats.get("docling", 0)),
            int(caption_stats.get("cache", 0)),
            int(caption_stats.get("vlm", 0)),
        )
    logger.info("Markdown written: %s", md_path)

    item["conversion_status"] = "success"
    item["md_path"] = str(md_path)
    item["md_sha256"] = hash_file(md_path, config.manifest.hash_algo)
    item["md_public_url"] = _public_url_for_path(md_path, config)
    if config.manifest.include_image_index:
        item["image_index"] = image_index
        item["image_count"] = len(image_index)
    else:
        item["image_index"] = []
        item["image_count"] = 0
    if docling_meta.get("json_path"):
        item["docling_json_path"] = docling_meta["json_path"]
    if docling_meta.get("zip_path"):
        item["docling_zip_path"] = docling_meta["zip_path"]


def _resolve_docling_failure(result: DoclingResult, config: AppConfig) -> str | None:
    if result.status and result.status != "success":
        return f"docling status={result.status}"

    errors = [err for err in result.errors if err]
    if errors:
        fatal_errors = _filter_nonfatal_ocr_errors(errors)
        if not fatal_errors:
            logging.getLogger(__name__).warning(
                "Docling OCR warnings ignored: %s",
                "; ".join(errors),
            )
            return None
        errors = fatal_errors
        if config.runtime.fail_on_missing_ocr_lang and _is_missing_ocr_lang_error(
            errors
        ):
            raise RuntimeError("missing tesseract language packs")
        if config.docling.on_docling_error == "skip_document":
            return "docling_error"
        raise RuntimeError("; ".join(errors))
    return None


def _is_missing_ocr_lang_error(errors: list[str]) -> bool:
    lowered = " ".join(errors).lower()
    return "tessdata" in lowered or "language" in lowered or "tesseract" in lowered


def _filter_nonfatal_ocr_errors(errors: list[str]) -> list[str]:
    fatal: list[str] = []
    for err in errors:
        lowered = err.lower()
        if (
            "osd failed" in lowered
            or "too few characters" in lowered
            or "invalid resolution" in lowered
        ):
            continue
        fatal.append(err)
    return fatal


def _extract_docling_output(
    result: DoclingResult,
    item: dict[str, Any],
    config: AppConfig,
    captioner: CaptionClient | None,
    caption_state: dict[str, Any],
) -> tuple[str | None, str | None, list[dict[str, Any]], dict[str, Any]]:
    md_text: str | None = None
    json_text: str | None = None
    image_index: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}

    if result.zip_bytes:
        md_text, json_text, image_index, meta = _extract_from_zip(
            result.zip_bytes, item, config, captioner, caption_state
        )
    else:
        md_text = result.md_content
        if isinstance(result.json_content, str):
            json_text = result.json_content
        elif result.json_content is not None:
            json_text = json.dumps(result.json_content, ensure_ascii=False, indent=2)

    if json_text is not None:
        json_path = config.output.docling_json_dir / Path(
            item["source_rel_path"]
        ).with_suffix(".json")
        write_text(json_path, json_text)
        meta["json_path"] = str(json_path)

    return md_text, json_text, image_index, meta


def _extract_from_zip(
    zip_bytes: bytes,
    item: dict[str, Any],
    config: AppConfig,
    captioner: CaptionClient | None,
    caption_state: dict[str, Any],
) -> tuple[str | None, str | None, list[dict[str, Any]], dict[str, Any]]:
    md_text = None
    json_text = None
    image_index: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}

    rel_source = Path(item["source_rel_path"])
    doc_root = rel_source.with_suffix("")

    if config.output.keep_zip:
        zip_path = config.output.docling_zip_dir / rel_source.with_suffix(".zip")
        ensure_dir(zip_path.parent)
        zip_path.write_bytes(zip_bytes)
        meta["zip_path"] = str(zip_path)

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        entries = [info for info in zf.infolist() if not info.is_dir()]
        names = [info.filename for info in entries]
        md_name = _pick_first(names, ".md")
        json_name = _pick_first(names, ".json")

        if md_name:
            md_text = _read_zip_text(zf, md_name)
        if json_name:
            json_text = _read_zip_text(zf, json_name)

        image_links, image_info = _extract_images(zf, entries, doc_root, config)
        if md_text and config.output.rewrite_docling_image_links:
            caption_map, title_map, caption_stats = _build_caption_map(
                json_text,
                image_info,
                captioner,
                caption_state,
                True,
            )
            figure_prefix = _figure_prefix(item, config)
            md_text, image_index = rewrite_markdown_images_with_placeholders(
                md_text,
                image_links,
                caption_map,
                title_map,
                include_caption_line=True,
                figure_prefix=figure_prefix,
            )
            meta["caption_stats"] = caption_stats
        elif md_text:
            image_index = []

    return md_text, json_text, image_index, meta


def _extract_images(
    zf: zipfile.ZipFile,
    entries: list[zipfile.ZipInfo],
    doc_root: Path,
    config: AppConfig,
) -> tuple[dict[str, str], dict[str, ImageInfo]]:
    link_map: dict[str, str] = {}
    image_info: dict[str, ImageInfo] = {}
    md_dir = None

    md_candidate = _pick_first([info.filename for info in entries], ".md")
    if md_candidate:
        md_dir = str(Path(md_candidate).parent)
        if md_dir == ".":
            md_dir = ""

    for info in entries:
        ext = Path(info.filename).suffix.lower()
        if ext not in IMAGE_EXTS:
            continue
        rel_in_zip = normalize_rel_path(info.filename)
        image_bytes = zf.read(info)
        info_obj = _store_image(
            image_bytes,
            ext,
            rel_in_zip,
            doc_root,
            config,
        )
        link_map[rel_in_zip] = info_obj.public_url
        image_info[rel_in_zip] = info_obj
        if md_dir:
            rel_to_md = normalize_rel_path(os.path.relpath(rel_in_zip, md_dir))
            link_map[rel_to_md] = info_obj.public_url
            image_info[rel_to_md] = info_obj

    return link_map, image_info


def _pick_first(names: list[str], suffix: str) -> str | None:
    candidates = [name for name in names if name.lower().endswith(suffix)]
    if not candidates:
        return None
    candidates.sort(key=lambda value: (len(value), value))
    return candidates[0]


def _read_zip_text(zf: zipfile.ZipFile, name: str) -> str:
    raw = zf.read(name)
    return raw.decode("utf-8", errors="replace")


def _build_caption_map(
    json_text: str | None,
    image_info: dict[str, ImageInfo],
    captioner: CaptionClient | None,
    caption_state: dict[str, Any],
    skip_on_error: bool,
) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    """Build caption and title maps for images.

    Returns:
        (caption_map, title_map, stats)
    """
    caption_map: dict[str, str] = {}
    title_map: dict[str, str] = {}
    text_lookup: dict[str, str] = {}
    stats = {"docling": 0, "cache": 0, "vlm": 0}

    if json_text:
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError:
            payload = {}

        texts = payload.get("texts", [])
        for idx, item in enumerate(texts):
            ref = item.get("self_ref") or f"#/texts/{idx}"
            text_value = (item.get("text") or item.get("orig") or "").strip()
            if text_value:
                text_lookup[ref] = _normalize_caption_text(text_value)

        for pic in payload.get("pictures", []):
            image = pic.get("image") or {}
            uri = image.get("uri")
            if not uri:
                continue
            caption = _caption_from_annotations(pic.get("annotations", []))
            if not caption:
                caption = _caption_from_refs(pic.get("captions", []), text_lookup)
            if not caption:
                continue
            caption = _normalize_caption_text(caption)
            if _is_bad_caption(caption):
                continue
            caption_map[normalize_rel_path(uri)] = caption
            caption_map[uri] = caption
            stats["docling"] += 1

    if not captioner:
        return caption_map, stats

    cache_items = caption_state.get("items")
    if not isinstance(cache_items, dict):
        cache_items = {}
        caption_state["items"] = cache_items

    images_by_hash: dict[str, list[str]] = {}
    for rel_path, info in image_info.items():
        images_by_hash.setdefault(info.image_hash, []).append(rel_path)

    logger = logging.getLogger(__name__)
    for image_hash, rel_paths in images_by_hash.items():
        existing = _find_existing_caption(caption_map, rel_paths)
        if existing:
            # For existing captions from docling, generate fallback title
            existing_title = _fallback_title(existing)
            _apply_caption_aliases(caption_map, title_map, rel_paths, existing, existing_title)
            continue

        cached = cache_items.get(image_hash)
        cached_caption = cached.get("caption") if isinstance(cached, dict) else None
        cached_title = cached.get("title") if isinstance(cached, dict) else None

        if cached_caption:
            normalized = _normalize_caption_text(str(cached_caption))
            if not _is_bad_caption(normalized):
                stats["cache"] += 1
                # Use cached title if available, otherwise fallback
                title = cached_title if cached_title else _fallback_title(normalized)
                _apply_caption_aliases(caption_map, title_map, rel_paths, normalized, title)
                continue

        info = image_info[rel_paths[0]]
        try:
            image_bytes = info.local_path.read_bytes()
            mime = _mime_for_ext(info.ext)

            # Generate detailed caption
            caption = captioner.describe_bytes(image_bytes, mime)
            stats["vlm"] += 1

            # Generate short title (if title_prompt is configured)
            title = captioner.generate_title(image_bytes, mime)
            if title:
                stats["vlm"] += 1
                title = _normalize_caption_text(title)

        except Exception as exc:
            if skip_on_error:
                raise CaptioningError(str(exc))
            logger.warning("VLM captioning failed: %s", exc)
            continue

        if not caption:
            if skip_on_error:
                raise CaptioningError("empty caption")
            continue

        caption = _normalize_caption_text(caption)
        if _is_bad_caption(caption):
            if skip_on_error:
                raise CaptioningError("bad caption")
            continue

        # Use title if available, otherwise fallback
        if not title or _is_bad_caption(title):
            title = _fallback_title(caption)

        cache_items[image_hash] = {
            "caption": caption,
            "title": title,
            "model": captioner.model,
            "prompt": captioner.prompt,
        }
        _apply_caption_aliases(caption_map, title_map, rel_paths, caption, title)

    return caption_map, title_map, stats


def _caption_from_annotations(annotations: list[dict[str, Any]]) -> str | None:
    for ann in annotations:
        if ann.get("kind") != "description":
            continue
        text = (ann.get("text") or "").strip()
        if text:
            return text
    return None


def _caption_from_refs(refs: list[Any], text_lookup: dict[str, str]) -> str | None:
    pieces = []
    for ref in refs:
        if isinstance(ref, dict):
            ref_id = str(ref.get("$ref") or "")
        else:
            ref_id = str(ref)
        if not ref_id:
            continue
        text = text_lookup.get(ref_id, "").strip()
        if text:
            pieces.append(text)
    if pieces:
        return " ".join(pieces)
    return None


_LEADING_STRIP_CHARS = " \t\r\n-–—:：,，。"

# Language-agnostic filler patterns (instead of hardcoded word lists)
_FILLER_PATTERNS = [
    # Match single words followed by comma/colon (e.g., "OK," "Sure," "好的，")
    r'^[A-Za-z\u4e00-\u9fff]{1,10}[,，]\s*',
    # Match "Here is/are" "Below is/are" "The following is/are"
    r'^(here|below|the\s+following)\s+(is|are)\s*[:：]?\s*',
    # Match Chinese courtesy phrases
    r'^(好的|当然|可以|没问题|以下是|下面是)[，,：:]?\s*',
]


def _strip_leading_fillers(text: str) -> str:
    """Strip conversational fillers using regex patterns (language-agnostic)."""
    cleaned = text.strip()

    for _ in range(3):  # Max 3 iterations
        original = cleaned
        for pattern in _FILLER_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.lstrip(_LEADING_STRIP_CHARS)

        # If no change, we're done
        if cleaned == original:
            break

    return cleaned.strip()


def _normalize_caption_text(text: str) -> str:
    cleaned = " ".join(text.split())
    cleaned = _strip_leading_fillers(cleaned)
    return cleaned.strip(_LEADING_STRIP_CHARS)


def _fallback_title(caption: str) -> str:
    """Generate fallback title from caption (language-agnostic)."""
    if not caption:
        return "Image"

    caption = caption.strip()

    # Strategy 1: If already short, use as-is
    if len(caption) <= 15:
        return caption

    # Strategy 2: Cut at first sentence-ending punctuation
    for sep in ['. ', '。', '! ', '！', '? ', '？']:
        if sep in caption:
            first_sentence = caption.split(sep)[0]
            if 3 <= len(first_sentence) <= 30:
                return first_sentence

    # Strategy 3: Cut at first comma
    for sep in [', ', '，', '; ', '；']:
        if sep in caption:
            first_part = caption.split(sep)[0]
            if 3 <= len(first_part) <= 30:
                return first_part

    # Strategy 4: Take first 20 characters
    return caption[:20]


def _is_bad_caption(text: str) -> bool:
    """Check if caption is invalid or error response (language-agnostic)."""
    if not text or len(text.strip()) < 3:
        return True

    lowered = text.lower().strip(_LEADING_STRIP_CHARS)

    # Generic invalid captions (any language)
    generic_bad = {
        "image", "figure", "picture", "photo",  # English
        "图片", "图像", "照片",  # Chinese
        "imagen", "foto",  # Spanish
        "bild",  # German
    }

    if lowered in generic_bad:
        return True

    # Error response patterns (VLM refusal or failure)
    error_patterns = [
        r'(sorry|apolog|cannot|unable|can\'t)',  # English errors
        r'(抱歉|对不起|无法|不能)',  # Chinese errors
        r'(no image|not available|not provided|cannot see)',  # Missing image
        r'as an ai',  # AI self-reference
        r'(lo siento|no puedo)',  # Spanish errors
    ]

    for pattern in error_patterns:
        if re.search(pattern, lowered):
            return True

    return False


def _find_existing_caption(
    caption_map: dict[str, str], rel_paths: list[str]
) -> str | None:
    for rel in rel_paths:
        normalized = normalize_rel_path(rel)
        if normalized in caption_map:
            return caption_map[normalized]
        if rel in caption_map:
            return caption_map[rel]
    return None


def _apply_caption_aliases(
    caption_map: dict[str, str],
    title_map: dict[str, str],
    rel_paths: list[str],
    caption: str,
    title: str | None = None,
) -> None:
    """Apply caption and title to all path aliases."""
    for rel in rel_paths:
        normalized = normalize_rel_path(rel)
        caption_map.setdefault(normalized, caption)
        caption_map.setdefault(rel, caption)
        if title:
            title_map.setdefault(normalized, title)
            title_map.setdefault(rel, title)


def _mime_for_ext(ext: str) -> str:
    ext = ext.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext in {".tif", ".tiff"}:
        return "image/tiff"
    if ext == ".gif":
        return "image/gif"
    if ext == ".bmp":
        return "image/bmp"
    if ext == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _store_image(
    image_bytes: bytes,
    ext: str,
    rel_in_zip: str,
    doc_root: Path,
    config: AppConfig,
) -> ImageInfo:
    out_rel_path = normalize_rel_path(str(doc_root / rel_in_zip))
    out_path = config.output.images_dir / Path(out_rel_path)
    ensure_dir(out_path.parent)
    out_path.write_bytes(image_bytes)
    image_hash = _hash_bytes(image_bytes, config.manifest.hash_algo)
    return ImageInfo(
        image_hash=image_hash,
        local_path=out_path,
        public_url=_public_url_for_path(out_path, config),
        ext=ext,
    )


def _hash_bytes(data: bytes, algo: str) -> str:
    import hashlib

    hasher = hashlib.new(algo)
    hasher.update(data)
    return hasher.hexdigest()


def _figure_prefix(item: dict[str, Any], config: AppConfig) -> str:
    rel_path = str(item.get("source_rel_path") or "")
    if not rel_path:
        return "FIG"
    digest = _hash_bytes(rel_path.encode("utf-8"), config.manifest.hash_algo)
    return f"FIG-{digest[:12]}"


def _init_captioner(config: AppConfig) -> CaptionClient | None:
    api = config.captioning
    if not isinstance(api, dict):
        return None
    url = api.get("url")
    if not url:
        logging.getLogger(__name__).warning(
            "captioning.url missing; image captioning disabled"
        )
        return None
    headers = {str(k): str(v) for k, v in (api.get("headers") or {}).items()}
    params = api.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    model = str(params.get("model") or api.get("model") or "").strip()
    if not model:
        logging.getLogger(__name__).warning(
            "captioning.params.model missing; image captioning disabled"
        )
        return None
    prompt = str(api.get("prompt") or "Describe this image in a few sentences.")
    title_prompt = api.get("title_prompt")
    if title_prompt:
        title_prompt = str(title_prompt).strip() or None

    timeout_raw = api.get("timeout") or api.get("timeout_sec") or 30
    try:
        timeout_sec = float(timeout_raw)
    except (TypeError, ValueError):
        timeout_sec = 30.0
    return CaptionClient(
        CaptioningConfig(
            url=str(url),
            headers=headers,
            prompt=prompt,
            title_prompt=title_prompt,
            model=model,
            params=params,
            timeout_sec=timeout_sec,
        )
    )


def _load_caption_cache(path: Path, captioner: CaptionClient | None) -> dict[str, Any]:
    state = load_state(path)
    if not isinstance(state, dict):
        state = {"version": 1, "items": {}, "updated_at": None}
    if not isinstance(state.get("items"), dict):
        state["items"] = {}
    if captioner:
        meta = {"model": captioner.model, "prompt": captioner.prompt}
        if state.get("meta") != meta:
            state["items"] = {}
        state["meta"] = meta
    return state


def _save_caption_cache(
    path: Path, state: dict[str, Any], captioner: CaptionClient | None
) -> None:
    if not captioner:
        return
    state["updated_at"] = time.time()
    state["meta"] = {"model": captioner.model, "prompt": captioner.prompt}
    save_state(path, state)


def _apply_stage2_dedupe(items: list[dict[str, Any]], config: AppConfig) -> None:
    dedupe_key = _dedupe_field(config, "stage2", "md_sha256")
    canonical_strategy = _canonical_strategy(config)
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get("conversion_status") != "success":
            continue
        value = item.get(dedupe_key)
        if value:
            groups.setdefault(str(value), []).append(item)

    for group in groups.values():
        paths = [item["source_rel_path"] for item in group]
        canonical = _choose_canonical(paths, canonical_strategy)
        for item in group:
            item["canonical"] = item["source_rel_path"] == canonical
            item["canonical_rel_path"] = canonical

    rel_to_canonical = {
        item["source_rel_path"]: item.get("canonical_rel_path") for item in items
    }
    for item in items:
        if item.get("canonical_rel_path"):
            continue
        stage1_ref = item.get("stage1_canonical_rel_path")
        if stage1_ref:
            item["canonical_rel_path"] = rel_to_canonical.get(stage1_ref, stage1_ref)


def _assign_rag_metadata(items: list[dict[str, Any]], config: AppConfig) -> None:
    for item in items:
        if not item.get("canonical"):
            continue
        if item.get("conversion_status") != "success":
            continue
        file_source = _build_file_source(
            config.lightrag.file_source_prefix, item["source_rel_path"]
        )
        item["rag"] = {"file_source": file_source}


def _build_file_source(prefix: str, rel_path: str) -> str:
    prefix = prefix.rstrip("/")
    rel_path = normalize_rel_path(rel_path)
    if prefix:
        return f"{prefix}/{rel_path}"
    return rel_path


def _dedupe_field(config: AppConfig, key: str, default: str) -> str:
    dedupe = config.manifest.dedupe
    if isinstance(dedupe, dict):
        value = dedupe.get(key)
        if value:
            return str(value)
    return default


def _canonical_strategy(config: AppConfig) -> str:
    dedupe = config.manifest.dedupe
    if isinstance(dedupe, dict):
        value = dedupe.get("canonical_strategy")
        if value:
            return str(value)
    return "shortest_path"


def _choose_canonical(paths: list[str], strategy: str) -> str:
    if strategy == "shortest_path":
        return choose_canonical(paths)
    return choose_canonical(paths)


def _public_url_for_path(path: Path, config: AppConfig) -> str:
    rel_path = _rel_path(path, config.output.root_dir)
    return build_public_url(
        config.output.public_base_url, config.output.public_path_prefix, rel_path
    )


def _rel_path(path: Path, root_dir: Path) -> str:
    try:
        rel = path.relative_to(root_dir)
        return normalize_rel_path(rel.as_posix())
    except ValueError:
        return normalize_rel_path(path.as_posix())
