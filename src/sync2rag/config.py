from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


@dataclass
class InputConfig:
    root_dir: Path
    include_ext: list[str]
    passthrough_ext: list[str]
    exclude_globs: list[str]
    follow_symlinks: bool
    max_file_size_mb: int


@dataclass
class DoclingOptions:
    from_formats: list[str]
    to_formats: list[str]
    target_type: str
    image_export_mode: str
    include_images: bool
    images_scale: float
    do_ocr: bool
    force_ocr: bool
    ocr_engine: str
    ocr_lang: list[str]
    pdf_backend: str
    pipeline: str
    document_timeout: float | None


@dataclass
class DoclingConfig:
    base_url: str
    endpoint: str
    use_async: bool
    async_poll_interval_sec: int
    async_timeout_sec: float | None
    timeout_sec: int
    options: DoclingOptions
    on_vlm_error: str
    on_docling_error: str


@dataclass
class OutputConfig:
    root_dir: Path
    markdown_dir: Path
    docling_json_dir: Path
    docling_zip_dir: Path
    images_dir: Path
    keep_zip: bool
    public_base_url: str
    public_path_prefix: str
    rewrite_docling_image_links: bool
    rewrite_passthrough_md: bool
    image_dedupe: bool
    image_dedupe_dir: Path


@dataclass
class ManifestConfig:
    full_path: Path
    rag_path: Path
    include_image_index: bool
    hash_algo: str
    dedupe: dict[str, str]


@dataclass
class LightRAGConfig:
    base_url: str
    api_key: str
    batch_size: int
    list_page_size: int
    file_source_prefix: str
    delete_missing: bool
    update_on_change: bool
    wait_inflight: bool
    inflight_poll_sec: int
    delete_llm_cache: bool
    delete_file: bool


@dataclass
class RuntimeConfig:
    dry_run: bool
    log_level: str
    max_workers: int
    state_dir: Path
    fail_on_missing_ocr_lang: bool


@dataclass
class AppConfig:
    input: InputConfig
    docling: DoclingConfig
    output: OutputConfig
    manifest: ManifestConfig
    lightrag: LightRAGConfig
    runtime: RuntimeConfig
    captioning: dict[str, Any] | None


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    input_cfg = _parse_input(raw.get("input", {}))
    docling_cfg = _parse_docling(raw.get("docling", {}))
    output_cfg = _parse_output(raw.get("output", {}))
    manifest_cfg = _parse_manifest(raw.get("manifest", {}))
    lightrag_cfg = _parse_lightrag(raw.get("lightrag", {}))
    runtime_cfg = _parse_runtime(raw.get("runtime", {}))
    captioning_cfg = _parse_captioning(raw)

    _validate_paths(input_cfg, output_cfg)

    return AppConfig(
        input=input_cfg,
        docling=docling_cfg,
        output=output_cfg,
        manifest=manifest_cfg,
        lightrag=lightrag_cfg,
        runtime=runtime_cfg,
        captioning=captioning_cfg,
    )


def require_lightrag_config(config: AppConfig) -> None:
    if not config.lightrag.base_url:
        raise ConfigError("missing required key: lightrag.base_url")
    if not config.lightrag.api_key:
        raise ConfigError("missing required key: lightrag.api_key")


def _resolve_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_paths(input_cfg: InputConfig, output_cfg: OutputConfig) -> None:
    input_root = _resolve_path(input_cfg.root_dir)
    output_root = _resolve_path(output_cfg.root_dir)
    if _is_subpath(output_root, input_root) or _is_subpath(input_root, output_root):
        raise ConfigError(
            "input.root_dir and output.root_dir must not be nested; "
            "choose separate directories to avoid scanning generated outputs"
        )


def _parse_input(raw: dict[str, Any]) -> InputConfig:
    root_dir = _require(raw, "root_dir", "input")
    include_ext = _as_list(raw.get("include_ext", []))
    passthrough_ext = _as_list(raw.get("passthrough_ext", []))
    exclude_globs = _as_list(raw.get("exclude_globs", []))
    follow_symlinks = bool(raw.get("follow_symlinks", False))
    max_file_size_mb = int(raw.get("max_file_size_mb", 500))

    root_dir = Path(root_dir)
    if not root_dir.exists():
        raise ConfigError(f"input.root_dir does not exist: {root_dir}")

    return InputConfig(
        root_dir=root_dir,
        include_ext=_normalize_exts(include_ext),
        passthrough_ext=_normalize_exts(passthrough_ext),
        exclude_globs=exclude_globs,
        follow_symlinks=follow_symlinks,
        max_file_size_mb=max_file_size_mb,
    )


def _parse_docling(raw: dict[str, Any]) -> DoclingConfig:
    base_url = _require(raw, "base_url", "docling")
    endpoint = raw.get("endpoint", "file")
    use_async = bool(raw.get("use_async", True))
    async_poll_interval_sec = int(raw.get("async_poll_interval_sec", 5))
    async_timeout_raw = raw.get("async_timeout_sec", 3600)
    async_timeout_sec = None
    if async_timeout_raw is not None:
        async_timeout_sec = float(async_timeout_raw)
        if async_timeout_sec <= 0:
            async_timeout_sec = None
    timeout_sec = int(raw.get("timeout_sec", 600))
    on_vlm_error = raw.get("on_vlm_error", "skip_document")
    on_docling_error = raw.get("on_docling_error", "skip_document")

    options_raw = raw.get("options", {})
    target_type = options_raw.get("target_type", options_raw.get("target", "zip"))
    document_timeout_raw = options_raw.get("document_timeout")
    document_timeout = float(document_timeout_raw) if document_timeout_raw is not None else None

    options = DoclingOptions(
        from_formats=_as_list(options_raw.get("from_formats", [])),
        to_formats=_as_list(options_raw.get("to_formats", ["md"])),
        target_type=str(target_type),
        image_export_mode=str(options_raw.get("image_export_mode", "referenced")),
        include_images=bool(options_raw.get("include_images", True)),
        images_scale=float(options_raw.get("images_scale", 2.0)),
        do_ocr=bool(options_raw.get("do_ocr", True)),
        force_ocr=bool(options_raw.get("force_ocr", False)),
        ocr_engine=str(options_raw.get("ocr_engine", "tesseract")),
        ocr_lang=_as_list(options_raw.get("ocr_lang", [])),
        pdf_backend=str(options_raw.get("pdf_backend", "dlparse_v4")),
        pipeline=str(options_raw.get("pipeline", "standard")),
        document_timeout=document_timeout,
    )

    return DoclingConfig(
        base_url=base_url,
        endpoint=endpoint,
        use_async=use_async,
        async_poll_interval_sec=async_poll_interval_sec,
        async_timeout_sec=async_timeout_sec,
        timeout_sec=timeout_sec,
        options=options,
        on_vlm_error=on_vlm_error,
        on_docling_error=on_docling_error,
    )


def _parse_output(raw: dict[str, Any]) -> OutputConfig:
    root_dir = Path(raw.get("root_dir", "data"))
    markdown_dir = Path(raw.get("markdown_dir", root_dir / "markdown"))
    docling_json_dir = Path(raw.get("docling_json_dir", root_dir / "docling" / "json"))
    docling_zip_dir = Path(raw.get("docling_zip_dir", root_dir / "docling" / "zip"))
    images_dir = Path(raw.get("images_dir", root_dir / "docling" / "images"))
    image_dedupe_dir = Path(raw.get("image_dedupe_dir", images_dir / "_dedup"))

    return OutputConfig(
        root_dir=root_dir,
        markdown_dir=markdown_dir,
        docling_json_dir=docling_json_dir,
        docling_zip_dir=docling_zip_dir,
        images_dir=images_dir,
        keep_zip=bool(raw.get("keep_zip", True)),
        public_base_url=str(raw.get("public_base_url", "")),
        public_path_prefix=str(raw.get("public_path_prefix", "")),
        rewrite_docling_image_links=bool(raw.get("rewrite_docling_image_links", True)),
        rewrite_passthrough_md=bool(raw.get("rewrite_passthrough_md", False)),
        image_dedupe=bool(raw.get("image_dedupe", False)),
        image_dedupe_dir=image_dedupe_dir,
    )


def _parse_manifest(raw: dict[str, Any]) -> ManifestConfig:
    full_path = Path(raw.get("full_path", "manifests/manifest.json"))
    rag_path = Path(raw.get("rag_path", "manifests/manifest.rag.json"))

    return ManifestConfig(
        full_path=full_path,
        rag_path=rag_path,
        include_image_index=bool(raw.get("include_image_index", True)),
        hash_algo=str(raw.get("hash_algo", "sha256")),
        dedupe=raw.get("dedupe", {"stage1": "source_sha256", "stage2": "md_sha256", "canonical_strategy": "shortest_path"}),
    )


def _parse_lightrag(raw: dict[str, Any]) -> LightRAGConfig:
    return LightRAGConfig(
        base_url=str(raw.get("base_url", "")),
        api_key=str(raw.get("api_key", "")),
        batch_size=int(raw.get("batch_size", 20)),
        list_page_size=int(raw.get("list_page_size", 200)),
        file_source_prefix=str(raw.get("file_source_prefix", "sync2rag")),
        delete_missing=bool(raw.get("delete_missing", True)),
        update_on_change=bool(raw.get("update_on_change", True)),
        wait_inflight=bool(raw.get("wait_inflight", False)),
        inflight_poll_sec=int(raw.get("inflight_poll_sec", 5)),
        delete_llm_cache=bool(raw.get("delete_llm_cache", False)),
        delete_file=bool(raw.get("delete_file", False)),
    )


def _parse_runtime(raw: dict[str, Any]) -> RuntimeConfig:
    return RuntimeConfig(
        dry_run=bool(raw.get("dry_run", False)),
        log_level=str(raw.get("log_level", "INFO")),
        max_workers=int(raw.get("max_workers", 4)),
        state_dir=Path(raw.get("state_dir", ".state")),
        fail_on_missing_ocr_lang=bool(raw.get("fail_on_missing_ocr_lang", True)),
    )


def _parse_captioning(raw: dict[str, Any]) -> dict[str, Any] | None:
    captioning = raw.get("captioning")
    if isinstance(captioning, dict):
        return captioning
    return None


def _require(raw: dict[str, Any], key: str, section: str) -> Any:
    if key not in raw:
        raise ConfigError(f"missing required key: {section}.{key}")
    return raw[key]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _normalize_exts(items: list[str]) -> list[str]:
    normalized = []
    for item in items:
        if not item:
            continue
        item = item.strip()
        if not item:
            continue
        if not item.startswith("."):
            item = "." + item
        normalized.append(item.lower())
    return normalized
