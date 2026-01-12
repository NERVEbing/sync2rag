from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class LightRAGDoc:
    doc_id: str
    file_path: str
    status: str


class LightRAGClient:
    def __init__(self, base_url: str, api_key: str, timeout_sec: int = 60) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_sec)
        self._headers = {"X-API-Key": api_key} if api_key else {}

    def get_pipeline_status(self) -> dict[str, Any]:
        url = f"{self._base_url}/documents/pipeline_status"
        response = self._client.get(url, headers=self._headers)
        response.raise_for_status()
        return response.json()

    def list_documents(self, page_size: int) -> list[LightRAGDoc]:
        page = 1
        docs: list[LightRAGDoc] = []
        while True:
            payload = {
                "page": page,
                "page_size": page_size,
                "sort_field": "updated_at",
                "sort_direction": "desc",
            }
            response = self._client.post(
                f"{self._base_url}/documents/paginated",
                json=payload,
                headers=self._headers,
            )
            response.raise_for_status()
            data = response.json()
            for item in data.get("documents", []):
                docs.append(
                    LightRAGDoc(
                        doc_id=item.get("id"),
                        file_path=item.get("file_path"),
                        status=item.get("status"),
                    )
                )
            pagination = data.get("pagination", {})
            if not pagination.get("has_next"):
                break
            page += 1
        return docs

    def delete_documents(self, doc_ids: list[str], delete_file: bool, delete_llm_cache: bool) -> dict[str, Any]:
        url = f"{self._base_url}/documents/delete_document"
        payload = {
            "doc_ids": doc_ids,
            "delete_file": delete_file,
            "delete_llm_cache": delete_llm_cache,
        }
        response = self._client.request("DELETE", url, json=payload, headers=self._headers)
        response.raise_for_status()
        return response.json()

    def insert_texts(self, texts: list[str], file_sources: list[str] | None) -> dict[str, Any]:
        url = f"{self._base_url}/documents/texts"
        payload: dict[str, Any] = {"texts": texts}
        if file_sources is not None:
            payload["file_sources"] = file_sources
        response = self._client.post(url, json=payload, headers=self._headers)
        response.raise_for_status()
        return response.json()
