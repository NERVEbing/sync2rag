from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import DoclingConfig


@dataclass
class DoclingResult:
    status: str
    task_id: str | None
    md_content: str | None
    json_content: dict[str, Any] | None
    zip_bytes: bytes | None
    processing_time: float | None
    errors: list[str]


class DoclingClient:
    def __init__(self, config: DoclingConfig) -> None:
        self._config = config
        self._client = httpx.Client(timeout=config.timeout_sec)

    def convert_file(self, file_path: Path) -> DoclingResult:
        if self._config.use_async:
            return self._convert_file_async(file_path)
        return self._convert_file_sync(file_path)

    def close(self) -> None:
        self._client.close()

    def _convert_file_sync(self, file_path: Path) -> DoclingResult:
        base_url = self._config.base_url.rstrip("/")
        url = f"{base_url}/v1/convert/{self._config.endpoint}"
        fields = _build_form_fields(self._config.options)
        with file_path.open("rb") as handle:
            files = {"files": (file_path.name, handle, "application/octet-stream")}
            response = self._client.post(url, data=fields, files=files)
        response.raise_for_status()
        return _parse_result(response)

    def _convert_file_async(self, file_path: Path) -> DoclingResult:
        base_url = self._config.base_url.rstrip("/")
        url = f"{base_url}/v1/convert/{self._config.endpoint}/async"
        fields = _build_form_fields(self._config.options)
        with file_path.open("rb") as handle:
            files = {"files": (file_path.name, handle, "application/octet-stream")}
            response = self._client.post(url, data=fields, files=files)
        response.raise_for_status()
        task = response.json()
        task_id = task.get("task_id")
        if not task_id:
            return DoclingResult(
                status="failure",
                task_id=None,
                md_content=None,
                json_content=None,
                zip_bytes=None,
                processing_time=None,
                errors=["missing task_id"],
            )

        status = task.get("task_status")
        deadline = None
        if self._config.async_timeout_sec is not None:
            deadline = time.monotonic() + self._config.async_timeout_sec
        while status not in ("success", "failure"):
            if deadline is not None and time.monotonic() >= deadline:
                return DoclingResult(
                    status="failure",
                    task_id=task_id,
                    md_content=None,
                    json_content=None,
                    zip_bytes=None,
                    processing_time=None,
                    errors=["docling async timeout"],
                )
            time.sleep(self._config.async_poll_interval_sec)
            poll = self._client.get(f"{base_url}/v1/status/poll/{task_id}")
            poll.raise_for_status()
            task = poll.json()
            status = task.get("task_status")

        if status != "success":
            return DoclingResult(
                status="failure",
                task_id=task_id,
                md_content=None,
                json_content=None,
                zip_bytes=None,
                processing_time=None,
                errors=["docling async task failed"],
            )

        result = self._client.get(f"{base_url}/v1/result/{task_id}")
        result.raise_for_status()
        parsed = _parse_result(result)
        parsed.task_id = task_id
        return parsed


def _parse_result(response: httpx.Response) -> DoclingResult:
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        data = response.json()
        document = data.get("document", {}) if isinstance(data, dict) else {}
        return DoclingResult(
            status=str(data.get("status", "success")),
            task_id=None,
            md_content=document.get("md_content"),
            json_content=document.get("json_content"),
            zip_bytes=None,
            processing_time=data.get("processing_time"),
            errors=[str(err) for err in data.get("errors", [])],
        )

    return DoclingResult(
        status="success",
        task_id=None,
        md_content=None,
        json_content=None,
        zip_bytes=response.content,
        processing_time=None,
        errors=[],
    )


def _build_form_fields(options: Any) -> dict[str, str | list[str]]:
    fields: dict[str, str | list[str]] = {}
    for key, value in _options_to_pairs(options).items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            fields[key] = [_stringify(item) for item in value]
        elif isinstance(value, dict):
            fields[key] = json.dumps(value)
        else:
            fields[key] = _stringify(value)
    return fields


def _options_to_pairs(options: Any) -> dict[str, Any]:
    return {
        "from_formats": options.from_formats,
        "to_formats": options.to_formats,
        "target_type": options.target_type,
        "image_export_mode": options.image_export_mode,
        "include_images": options.include_images,
        "images_scale": options.images_scale,
        "do_ocr": options.do_ocr,
        "force_ocr": options.force_ocr,
        "ocr_engine": options.ocr_engine,
        "ocr_lang": options.ocr_lang,
        "pdf_backend": options.pdf_backend,
        "pipeline": options.pipeline,
        "document_timeout": options.document_timeout,
    }


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
