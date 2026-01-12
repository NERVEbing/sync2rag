from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .config import AppConfig, require_lightrag_config
from .lightrag_client import LightRAGClient, LightRAGDoc
from .manifest import load_manifest
from .state import load_state, save_state
from .utils import read_text


INFLIGHT_STATUSES = {"pending", "processing", "queueing", "queued", "running", "in_progress"}
FAILED_STATUSES = {"failed", "error"}


def sync_lightrag(config: AppConfig, manifest_path: Path | None = None) -> dict[str, Any]:
    logger = logging.getLogger(__name__)
    require_lightrag_config(config)
    rag_path = manifest_path or config.manifest.rag_path
    rag_manifest = load_manifest(rag_path)
    items = rag_manifest.get("items", [])

    local_items = {item["file_source"]: item for item in items if item.get("file_source")}
    logger.info(
        "Sync start: local=%d, delete_missing=%s, update_on_change=%s, wait_inflight=%s",
        len(local_items),
        config.lightrag.delete_missing,
        config.lightrag.update_on_change,
        config.lightrag.wait_inflight,
    )
    state_path = config.runtime.state_dir / "lightrag_index.json"
    state = load_state(state_path)
    state_items = state.get("items", {})

    client = LightRAGClient(config.lightrag.base_url, config.lightrag.api_key)

    if config.lightrag.wait_inflight:
        _wait_for_pipeline_idle(client, config)

    remote_docs = client.list_documents(config.lightrag.list_page_size)
    remote_map = {
        doc.file_path: doc
        for doc in remote_docs
        if _has_prefix(doc.file_path, config.lightrag.file_source_prefix)
    }
    logger.info("Sync remote: total=%d", len(remote_map))

    to_delete: list[LightRAGDoc] = []
    to_reupload: list[dict[str, Any]] = []
    skipped_inflight = 0

    for file_source, doc in remote_map.items():
        if file_source in local_items:
            continue
        if not config.lightrag.delete_missing:
            continue
        if _is_inflight(doc.status) and not config.lightrag.wait_inflight:
            skipped_inflight += 1
            continue
        to_delete.append(doc)

    for file_source, item in local_items.items():
        md_path = item.get("md_path")
        if not md_path:
            logger.warning("Missing md_path for %s", file_source)
            continue
        md_sha = item.get("md_sha256")
        doc = remote_map.get(file_source)
        if doc:
            if _is_inflight(doc.status) and not config.lightrag.wait_inflight:
                skipped_inflight += 1
                continue
            if _is_failed(doc.status):
                to_reupload.append(item)
            elif config.lightrag.update_on_change:
                state_sha = _state_sha(state_items, file_source)
                if state_sha != md_sha:
                    to_reupload.append(item)
        else:
            to_reupload.append(item)

    logger.info(
        "Sync plan: delete=%d, upload=%d, skipped_inflight=%d",
        len(to_delete),
        len(to_reupload),
        skipped_inflight,
    )

    deleted_count = 0
    if to_delete and not config.runtime.dry_run:
        deleted_count += _delete_docs(client, to_delete, config)
    elif to_delete:
        logger.info("Dry run: %d documents queued for deletion", len(to_delete))

    if to_reupload:
        deleted_count += _delete_before_reupload(client, to_reupload, remote_map, config)
        uploaded_sources = _upload_docs(client, to_reupload, config)
    else:
        uploaded_sources = []

    if not config.runtime.dry_run:
        for doc in to_delete:
            state_items.pop(doc.file_path, None)
        for item in to_reupload:
            file_source = item.get("file_source")
            if not file_source or file_source not in uploaded_sources:
                continue
            state_items[file_source] = {
                "md_sha256": item.get("md_sha256"),
                "md_path": item.get("md_path"),
            }
        state["items"] = state_items
        state["updated_at"] = time.time()
        save_state(state_path, state)

    summary = {
        "deleted": deleted_count,
        "uploaded": len(uploaded_sources),
        "skipped_inflight": skipped_inflight,
        "total_local": len(local_items),
        "total_remote": len(remote_map),
    }
    logger.info(
        "Sync done: deleted=%d, uploaded=%d, skipped_inflight=%d, total_local=%d, total_remote=%d",
        summary["deleted"],
        summary["uploaded"],
        summary["skipped_inflight"],
        summary["total_local"],
        summary["total_remote"],
    )
    return summary


def _has_prefix(value: str | None, prefix: str) -> bool:
    if not value:
        return False
    prefix = prefix.rstrip("/")
    if not prefix:
        return True
    return value.startswith(prefix)


def _is_inflight(status: str | None) -> bool:
    if not status:
        return False
    lowered = status.lower()
    if lowered in INFLIGHT_STATUSES:
        return True
    return "pending" in lowered or "processing" in lowered or "queue" in lowered or "running" in lowered


def _is_failed(status: str | None) -> bool:
    if not status:
        return False
    lowered = status.lower()
    if lowered in FAILED_STATUSES:
        return True
    return "fail" in lowered or "error" in lowered


def _state_sha(state_items: dict[str, Any], file_source: str) -> str | None:
    entry = state_items.get(file_source)
    if isinstance(entry, dict):
        return entry.get("md_sha256")
    return None


def _wait_for_pipeline_idle(client: LightRAGClient, config: AppConfig) -> None:
    logger = logging.getLogger(__name__)
    while True:
        status = client.get_pipeline_status()
        inflight = _count_inflight(status)
        if inflight <= 0:
            return
        logger.info("Waiting for inflight tasks: %s", inflight)
        time.sleep(config.lightrag.inflight_poll_sec)


def _count_inflight(payload: Any) -> int:
    if isinstance(payload, dict):
        total = 0
        for key, value in payload.items():
            total += _count_inflight(value)
            if isinstance(value, (int, float)) and _is_inflight_key(key):
                total += int(value)
        return total
    if isinstance(payload, list):
        return sum(_count_inflight(value) for value in payload)
    return 0


def _is_inflight_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ("pending", "processing", "running", "queue", "inflight"))


def _delete_docs(client: LightRAGClient, docs: list[LightRAGDoc], config: AppConfig) -> int:
    logger = logging.getLogger(__name__)
    deleted = 0
    batch_size = config.lightrag.batch_size
    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]
        ids = [doc.doc_id for doc in batch if doc.doc_id]
        if not ids:
            continue
        client.delete_documents(ids, config.lightrag.delete_file, config.lightrag.delete_llm_cache)
        deleted += len(ids)
        logger.info("Deleted %d documents", len(ids))
    return deleted


def _delete_before_reupload(
    client: LightRAGClient,
    items: list[dict[str, Any]],
    remote_map: dict[str, LightRAGDoc],
    config: AppConfig,
) -> int:
    if config.runtime.dry_run:
        return 0
    docs_to_delete = []
    for item in items:
        file_source = item.get("file_source")
        if not file_source:
            continue
        doc = remote_map.get(file_source)
        if doc and doc.doc_id:
            docs_to_delete.append(doc)
    return _delete_docs(client, docs_to_delete, config)


def _upload_docs(client: LightRAGClient, items: list[dict[str, Any]], config: AppConfig) -> list[str]:
    logger = logging.getLogger(__name__)
    if config.runtime.dry_run:
        logger.info("Dry run: %d documents queued for upload", len(items))
        return []

    uploaded_sources: list[str] = []
    batch_size = config.lightrag.batch_size
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        texts: list[str] = []
        file_sources: list[str] = []
        for item in batch:
            md_path = Path(item["md_path"])
            try:
                texts.append(read_text(md_path))
            except FileNotFoundError:
                logger.warning("Markdown file not found: %s", md_path)
                continue
            file_sources.append(item["file_source"])
        if not texts:
            continue
        client.insert_texts(texts, file_sources)
        uploaded_sources.extend(file_sources)
        logger.info("Uploaded %d documents", len(texts))
    return uploaded_sources
