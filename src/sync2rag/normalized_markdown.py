from __future__ import annotations

import logging
import re
from typing import Any, Callable
from urllib.parse import quote, unquote, urlparse, urlunparse


_IMAGE_REF_RE = re.compile(r"\[ImageRef:\s*(?P<fig>FIG-[^\]\s]+)\s*\]")
_IMAGE_REF_INLINE_RE = re.compile(r"ImageRef:\s*(?P<fig>FIG-[^\s]+)")
_MD_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)]+)\)")
_HTML_IMAGE_RE = re.compile(
    r"<img\s+[^>]*src=[\"'](?P<url>[^\"']+)[\"'][^>]*>", re.IGNORECASE
)
_HTML_ALT_RE = re.compile(r"alt=[\"']([^\"']*)[\"']", re.IGNORECASE)

_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
_NOISE_TOKEN_RE = re.compile(r"^[A-Z0-9/.-]+$")
_FREQ_UNIT_RE = re.compile(
    r"^\d+(\.\d+)?\s*(Hz|kHz|MHz|GHz|V|mV|A|mA|dB|dBm|dBuV|W|mW|%|Ohm|ohm)\b"
)

_SECTION_HEADING_RE = re.compile(r"^##\s*(images|figures)\b", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?]$")
_SEE_FIGURE_RE = re.compile(r"\(See figure \[(?P<fig_id>[^\]]+)\]:\s*(?P<caption>[^)]+)\)")
_STRAY_FIG_RE = re.compile(r"(?<!\[)FIG-[A-Za-z0-9-]+")

IMAGE_CAPTION_PREFIX = "**Image:**"


def normalize_markdown(
    md_text: str, image_index: list[dict[str, Any]] | None = None
) -> str:
    fig_caption_map = _build_figure_map(image_index or [])
    md_text = _normalize_images(md_text, fig_caption_map)
    md_text = _normalize_noise_lines(md_text)
    md_text = _normalize_tables(md_text)
    md_text = _normalize_paragraphs(md_text)
    md_text = inject_images_inline(md_text, image_index or [])
    md_text = _final_cleanup(md_text)
    return md_text


def inject_images_inline(
    md_text: str, image_index: list[dict[str, Any]]
) -> str:
    """Replace (See figure [FIG-xxx]: ...) placeholders with inline images."""
    logger = logging.getLogger(__name__)
    fig_to_image: dict[str, tuple[str, str, str]] = {}

    for entry in image_index:
        fig_id = str(entry.get("figure_id") or "").strip()
        caption = str(entry.get("caption") or "").strip()
        url = _encode_image_url(str(entry.get("image_public_url") or ""))
        title = str(entry.get("title") or "").strip()
        if fig_id and caption and url:
            fig_to_image[fig_id] = (url, title, caption)

    lines = md_text.splitlines()
    out_lines: list[str] = []
    matched_count = 0

    for line in lines:
        matches = list(_SEE_FIGURE_RE.finditer(line))
        if matches:
            for match in matches:
                fig_id = match.group("fig_id").strip()
                placeholder_caption = match.group("caption").strip()
                image_data = fig_to_image.get(fig_id)
                if image_data:
                    url, title, caption = image_data
                    alt_text = title if title else caption[:20]
                    out_lines.append(f"![{alt_text}]({url})")
                    out_lines.append("")
                    out_lines.append(f"{IMAGE_CAPTION_PREFIX} {caption}")
                    matched_count += 1
                else:
                    logger.warning(
                        "No matching figure_id '%s' (caption: '%s')",
                        fig_id,
                        placeholder_caption[:50],
                    )
        else:
            out_lines.append(line)

    if matched_count < len(fig_to_image):
        logger.warning(
            "Image injection: %d/%d images matched to placeholders",
            matched_count,
            len(fig_to_image),
        )

    return "\n".join(out_lines)


def _encode_image_url(url: str) -> str:
    """Encode URL path segments, avoiding double-encoding."""
    if not url:
        return url
    parsed = urlparse(url)
    decoded_path = unquote(parsed.path)
    encoded_path = quote(decoded_path, safe="/")
    return urlunparse(parsed._replace(path=encoded_path))


def _build_figure_map(image_index: list[dict[str, Any]]) -> dict[str, str]:
    fig_caption: dict[str, str] = {}
    for entry in image_index:
        fig_id = str(entry.get("figure_id") or "").strip()
        caption = str(entry.get("caption") or "").strip()
        if fig_id and caption and len(caption) >= 3:
            fig_caption[fig_id] = caption
    return fig_caption


def _normalize_images(md_text: str, figure_map: dict[str, str]) -> str:
    seen: set[str] = set()

    def replace_ref(match: re.Match[str]) -> str:
        fig_id = match.group("fig")
        caption = figure_map.get(fig_id, "").strip()
        if not caption:
            return ""
        seen.add(fig_id)
        return f"(See figure [{fig_id}]: {caption})"

    def replace_inline_ref(match: re.Match[str]) -> str:
        fig_id = match.group("fig")
        caption = figure_map.get(fig_id, "").strip()
        if not caption:
            return ""
        seen.add(fig_id)
        return f"(See figure [{fig_id}]: {caption})"

    def replace_md_image(match: re.Match[str]) -> str:
        return ""

    def replace_html_image(match: re.Match[str]) -> str:
        return ""

    md_text = _strip_auto_image_sections(md_text)
    md_text = _replace_outside_code(md_text, _IMAGE_REF_RE, replace_ref)
    md_text = _replace_outside_code(md_text, _IMAGE_REF_INLINE_RE, replace_inline_ref)
    md_text = _replace_outside_code(md_text, _MD_IMAGE_RE, replace_md_image)
    md_text = _replace_outside_code(md_text, _HTML_IMAGE_RE, replace_html_image)
    md_text = _STRAY_FIG_RE.sub("", md_text)
    return md_text


def _strip_auto_image_sections(md_text: str) -> str:
    lines = md_text.splitlines()
    sections: list[tuple[str | None, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_heading, current_lines
        if current_heading is None and not current_lines:
            return
        sections.append((current_heading, current_lines))
        current_heading = None
        current_lines = []

    for line in lines:
        if line.startswith("## "):
            flush()
            current_heading = line
            current_lines = [line]
        else:
            current_lines.append(line)
    flush()

    kept: list[str] = []
    for heading, block in sections:
        if heading and _SECTION_HEADING_RE.match(heading):
            if _section_has_image_markers(block):
                continue
        kept.extend(block)
    return "\n".join(kept)


def _section_has_image_markers(lines: list[str]) -> bool:
    for line in lines:
        if "![" in line or "Caption:" in line or "FIG-" in line or "[ImageRef:" in line:
            return True
    return False


def _replace_outside_code(
    md_text: str, pattern: re.Pattern[str], replacer: Callable[[re.Match[str]], str]
) -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    in_code = False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
        else:
            out.append(pattern.sub(replacer, line))
    return "\n".join(out)


def _normalize_noise_lines(md_text: str) -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    in_code = False
    for line in lines:
        if line.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code or _looks_like_table_row(line) or _looks_like_table_separator(line):
            out.append(line)
            continue
        if _is_noise_line(line):
            continue
        out.append(line)
    return "\n".join(out)


def _normalize_tables(md_text: str) -> str:
    lines = md_text.splitlines()
    out: list[str] = []
    in_code = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            in_code = not in_code
            out.append(line)
            i += 1
            continue
        if in_code:
            out.append(line)
            i += 1
            continue
        if (
            _looks_like_table_row(line)
            and i + 1 < len(lines)
            and _looks_like_table_separator(lines[i + 1])
        ):
            if not _has_table_leadin(out):
                if out and out[-1].strip():
                    out.append("")
                out.append("The following table summarizes the test results.")
                out.append("")
            while i < len(lines) and (_looks_like_table_row(lines[i]) or _looks_like_table_separator(lines[i])):
                out.append(lines[i])
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _normalize_paragraphs(md_text: str) -> str:
    lines = _remove_repeated_lines(md_text.splitlines())
    out_lines: list[str] = []
    paragraph: str | None = None
    in_code = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if paragraph:
                out_lines.append(paragraph)
                out_lines.append("")
                paragraph = None
            in_code = not in_code
            out_lines.append(line)
            i += 1
            continue
        if in_code:
            out_lines.append(line)
            i += 1
            continue
        if not line.strip():
            if paragraph:
                out_lines.append(paragraph)
                out_lines.append("")
                paragraph = None
            else:
                if out_lines and out_lines[-1] != "":
                    out_lines.append("")
            i += 1
            continue
        if _is_block_line(line):
            if paragraph:
                out_lines.append(paragraph)
                out_lines.append("")
                paragraph = None
            out_lines.append(line)
            i += 1
            continue
        merged = " ".join(part.strip() for part in line.splitlines() if part.strip())
        if paragraph is None:
            paragraph = merged
        else:
            if _should_merge_paragraph(paragraph, merged):
                paragraph = f"{paragraph} {merged}".strip()
            else:
                out_lines.append(paragraph)
                out_lines.append("")
                paragraph = merged
        i += 1

    if paragraph:
        out_lines.append(paragraph)
    return "\n".join(out_lines)


def _final_cleanup(md_text: str) -> str:
    lines = [line.rstrip() for line in md_text.splitlines()]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if blank:
                continue
            cleaned.append("")
            blank = True
        else:
            cleaned.append(line)
            blank = False
    return "\n".join(cleaned).strip() + "\n"


def _extract_html_alt(tag: str) -> str:
    match = _HTML_ALT_RE.search(tag)
    return match.group(1) if match else ""


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _FREQ_UNIT_RE.match(stripped):
        return True
    if stripped in ("BW", "PE"):
        return True
    if len(stripped) <= 12 and _NOISE_TOKEN_RE.match(stripped) and any(ch.isdigit() for ch in stripped):
        return True
    if len(stripped) <= 3 and stripped.isupper():
        return True
    return False


def _looks_like_table_separator(line: str) -> bool:
    return bool(_TABLE_SEPARATOR_RE.match(line.strip()))


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped:
        return False
    return stripped.startswith("|") or stripped.endswith("|") or stripped.count("|") >= 2


def _has_table_leadin(out_lines: list[str]) -> bool:
    for line in reversed(out_lines):
        if not line.strip():
            continue
        return _is_sentence_line(line)
    return False


def _is_sentence_line(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith(("#", "-", "*")):
        return False
    if _looks_like_table_row(stripped):
        return False
    return stripped.endswith((".", "!", "?", ":"))


def _remove_repeated_lines(lines: list[str]) -> list[str]:
    normalized = [_normalize_line(line) for line in lines]
    counts: dict[str, int] = {}
    for line, norm in zip(lines, normalized):
        if _line_is_repeat_candidate(line):
            counts[norm] = counts.get(norm, 0) + 1

    remove_set = {n for n, count in counts.items() if count >= 3}
    return [line for line, norm in zip(lines, normalized)
            if not (norm in remove_set and _line_is_repeat_candidate(line))]


def _line_is_repeat_candidate(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", "-", "*", "```")):
        return False
    if _looks_like_table_row(stripped) or _looks_like_table_separator(stripped):
        return False
    if len(stripped) > 80:
        return False
    return not _SENTENCE_END_RE.search(stripped)


def _normalize_line(line: str) -> str:
    return " ".join(line.strip().split())


def _is_block_line(line: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith(("#", ">")) or stripped.startswith(("- ", "* ", "+ ")):
        return True
    if re.match(r"^\d+\.\s+", stripped):
        return True
    return _looks_like_table_row(stripped) or _looks_like_table_separator(stripped)


def _should_merge_paragraph(current: str, incoming: str) -> bool:
    if not _SENTENCE_END_RE.search(current.strip()):
        return True
    return len(current.strip()) < 80 or len(incoming.strip()) < 40
