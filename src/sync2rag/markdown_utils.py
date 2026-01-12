from __future__ import annotations

import re
from typing import Any

from .utils import is_relative_url, normalize_rel_path


_MD_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)]+)\)")
_HTML_IMAGE_RE = re.compile(r"<img\s+[^>]*src=[\"'](?P<url>[^\"']+)[\"'][^>]*>", re.IGNORECASE)
_HTML_ALT_RE = re.compile(r"alt=[\"']([^\"']*)[\"']", re.IGNORECASE)


def rewrite_markdown_images(
    md_text: str,
    link_map: dict[str, str],
    caption_map: dict[str, str] | None = None,
    include_caption_line: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    normalized_map = {normalize_rel_path(k): v for k, v in link_map.items()}
    normalized_captions = {normalize_rel_path(k): v for k, v in (caption_map or {}).items()}
    image_index: list[dict[str, Any]] = []

    def replace_md(match: re.Match[str]) -> str:
        alt = match.group("alt")
        url = match.group("url").strip()
        clean = _clean_url(url)
        normalized = normalize_rel_path(clean)
        new_url = normalized_map.get(normalized)
        if new_url and is_relative_url(clean):
            caption = normalized_captions.get(normalized)
            final_alt = caption or alt
            image_index.append({"image_public_url": new_url, "caption": caption or alt})
            replacement = f"![{final_alt}]({new_url})"
            if caption and include_caption_line:
                replacement = f"{replacement}\n\nCaption: {caption}\n"
            return replacement
        return match.group(0)

    def replace_html(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = match.group("url").strip()
        clean = _clean_url(url)
        normalized = normalize_rel_path(clean)
        new_url = normalized_map.get(normalized)
        if new_url and is_relative_url(clean):
            alt = _extract_html_alt(raw)
            caption = normalized_captions.get(normalized)
            final_alt = caption or alt
            image_index.append({"image_public_url": new_url, "caption": caption or alt})
            updated = raw.replace(url, new_url)
            if caption and alt != final_alt:
                updated = _replace_html_alt(updated, final_alt)
            if caption and include_caption_line:
                return f"{updated}\n\nCaption: {caption}\n"
            return updated
        return raw

    md_text = _MD_IMAGE_RE.sub(replace_md, md_text)
    md_text = _HTML_IMAGE_RE.sub(replace_html, md_text)
    return md_text, image_index


def rewrite_markdown_images_with_placeholders(
    md_text: str,
    link_map: dict[str, str],
    caption_map: dict[str, str] | None = None,
    include_caption_line: bool = True,
    figure_prefix: str = "FIG",
    section_title: str = "Images (auto-caption)",
) -> tuple[str, list[dict[str, Any]]]:
    normalized_map = {normalize_rel_path(k): v for k, v in link_map.items()}
    normalized_captions = {normalize_rel_path(k): v for k, v in (caption_map or {}).items()}
    image_index: list[dict[str, Any]] = []
    figures: list[dict[str, Any]] = []

    def next_figure_id() -> str:
        return f"{figure_prefix}-{len(figures) + 1:03d}"

    def add_figure(alt: str, normalized: str, new_url: str) -> str:
        caption = normalized_captions.get(normalized)
        fig_id = next_figure_id()
        record = {
            "figure_id": fig_id,
            "image_public_url": new_url,
            "caption": caption or alt or fig_id,
            "raw_caption": caption,
            "alt": alt,
        }
        figures.append(record)
        image_index.append(
            {
                "image_public_url": new_url,
                "caption": caption or alt or fig_id,
                "figure_id": fig_id,
            }
        )
        return fig_id

    def replace_md(match: re.Match[str]) -> str:
        alt = match.group("alt")
        url = match.group("url").strip()
        clean = _clean_url(url)
        normalized = normalize_rel_path(clean)
        new_url = normalized_map.get(normalized)
        if new_url and is_relative_url(clean):
            fig_id = add_figure(alt, normalized, new_url)
            return f"[ImageRef: {fig_id}]"
        return match.group(0)

    def replace_html(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = match.group("url").strip()
        clean = _clean_url(url)
        normalized = normalize_rel_path(clean)
        new_url = normalized_map.get(normalized)
        if new_url and is_relative_url(clean):
            alt = _extract_html_alt(raw)
            fig_id = add_figure(alt, normalized, new_url)
            return f"[ImageRef: {fig_id}]"
        return raw

    md_text = _MD_IMAGE_RE.sub(replace_md, md_text)
    md_text = _HTML_IMAGE_RE.sub(replace_html, md_text)

    if figures:
        lines = ["", f"## {section_title}"]
        for figure in figures:
            fig_id = figure["figure_id"]
            caption = figure.get("raw_caption")
            alt = caption or figure.get("alt") or fig_id
            lines.append(f"### {fig_id}")
            lines.append(f"![{alt}]({figure['image_public_url']})")
            if caption and include_caption_line:
                lines.append("")
                lines.append(f"Caption: {caption}")
            lines.append("")
        md_text = f"{md_text.rstrip()}\n" + "\n".join(lines).rstrip() + "\n"

    return md_text, image_index




def _clean_url(value: str) -> str:
    if value.startswith("<") and value.endswith(">"):
        return value[1:-1]
    return value


def _extract_html_alt(tag: str) -> str:
    match = _HTML_ALT_RE.search(tag)
    if match:
        return match.group(1)
    return ""


def _replace_html_alt(tag: str, alt: str) -> str:
    if _HTML_ALT_RE.search(tag):
        return _HTML_ALT_RE.sub(f'alt=\"{alt}\"', tag)
    return tag
