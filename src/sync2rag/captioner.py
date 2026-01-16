from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class CaptioningConfig:
    url: str
    headers: dict[str, str]
    prompt: str
    title_prompt: str | None
    model: str
    params: dict[str, Any]
    timeout_sec: float


class CaptionClient:
    def __init__(self, config: CaptioningConfig) -> None:
        self._config = config
        self._client = httpx.Client(timeout=config.timeout_sec)

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def prompt(self) -> str:
        return self._config.prompt

    @property
    def title_prompt(self) -> str | None:
        return self._config.title_prompt

    def close(self) -> None:
        self._client.close()

    def describe_bytes(self, image_bytes: bytes, mime: str) -> str | None:
        """Generate detailed caption for the image."""
        return self._call_vlm(image_bytes, mime, self._config.prompt)

    def generate_title(self, image_bytes: bytes, mime: str) -> str | None:
        """Generate short title for the image (if title_prompt is configured)."""
        if not self._config.title_prompt:
            return None
        return self._call_vlm(image_bytes, mime, self._config.title_prompt)

    def _call_vlm(self, image_bytes: bytes, mime: str, prompt: str) -> str | None:
        """Internal method to call VLM API with given prompt."""
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"
        payload = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        for key, value in self._config.params.items():
            if key == "model":
                continue
            payload[key] = value
        if "max_tokens" not in payload and "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")

        response = self._client.post(self._config.url, headers=self._config.headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return _extract_content(data)


def _extract_content(data: Any) -> str | None:
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
    return None
