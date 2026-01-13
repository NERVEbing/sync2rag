from __future__ import annotations

import re
from typing import Any


_IMAGE_REF_RE = re.compile(r"\[ImageRef:\s*(?P<fig>FIG-[^\]\s]+)\s*\]")
_IMAGE_REF_INLINE_RE = re.compile(r"ImageRef:\s*(?P<fig>FIG-[^\s]+)")
_FIG_ID_RE = re.compile(r"\bFIG-[A-Za-z0-9-]+\b")
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


def normalize_markdown(
    md_text: str, image_index: list[dict[str, Any]] | None = None
) -> str:
    figures = _build_figure_map(image_index or [])
    md_text, used_figures = _normalize_images(md_text, figures)
    md_text = _normalize_noise_lines(md_text)
    md_text = _normalize_tables(md_text)
    md_text = _normalize_paragraphs(md_text)
    md_text = _append_figures(md_text, used_figures)
    md_text = _final_cleanup(md_text)
    return md_text


def _build_figure_map(image_index: list[dict[str, Any]]) -> dict[str, str]:
    figures: dict[str, str] = {}
    for entry in image_index:
        fig_id = str(entry.get("figure_id") or "").strip()
        caption = str(entry.get("caption") or "").strip()
        if not fig_id or not caption or len(caption) < 3:
            continue
        figures[fig_id] = caption
    return figures


def _normalize_images(md_text: str, figure_map: dict[str, str]) -> tuple[str, list[str]]:
    used_figures: list[str] = []
    seen: set[str] = set()

    def replace_ref(match: re.Match[str]) -> str:
        fig_id = match.group("fig")
        caption = figure_map.get(fig_id, "").strip()
        if not caption:
            return ""
        if fig_id not in seen:
            used_figures.append(caption)
            seen.add(fig_id)
        return f"(See figure: {caption})"

    def replace_inline_ref(match: re.Match[str]) -> str:
        fig_id = match.group("fig")
        caption = figure_map.get(fig_id, "").strip()
        if not caption:
            return ""
        if fig_id not in seen:
            used_figures.append(caption)
            seen.add(fig_id)
        return f"(See figure: {caption})"

    def replace_md_image(match: re.Match[str]) -> str:
        alt = match.group("alt").strip()
        if not alt or len(alt) < 3:
            return ""
        used_figures.append(alt)
        return f"(See figure: {alt})"

    def replace_html_image(match: re.Match[str]) -> str:
        raw = match.group(0)
        alt = _extract_html_alt(raw).strip()
        if not alt or len(alt) < 3:
            return ""
        used_figures.append(alt)
        return f"(See figure: {alt})"

    md_text = _strip_auto_image_sections(md_text)
    md_text = _replace_outside_code(md_text, _IMAGE_REF_RE, replace_ref)
    md_text = _replace_outside_code(md_text, _IMAGE_REF_INLINE_RE, replace_inline_ref)
    md_text = _replace_outside_code(md_text, _MD_IMAGE_RE, replace_md_image)
    md_text = _replace_outside_code(md_text, _HTML_IMAGE_RE, replace_html_image)
    md_text = _FIG_ID_RE.sub("", md_text)
    return md_text, used_figures


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
        if (
            "![" in line
            or "Caption:" in line
            or "FIG-" in line
            or "[ImageRef:" in line
        ):
            return True
    return False


def _replace_outside_code(
    md_text: str, pattern: re.Pattern[str], replacer: Any
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
    lines = md_text.splitlines()
    lines = _remove_repeated_lines(lines)

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


def _append_figures(md_text: str, figures: list[str]) -> str:
    cleaned = [cap.strip() for cap in figures if cap and len(cap.strip()) >= 3]
    if not cleaned:
        return md_text
    lines = [md_text.rstrip(), "", "## Figures"]
    for idx, caption in enumerate(cleaned, start=1):
        lines.append(f"Figure {idx}: {caption}")
    return "\n".join(lines).rstrip() + "\n"


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
    if match:
        return match.group(1)
    return ""


def _is_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _FREQ_UNIT_RE.match(stripped):
        return True
    if stripped == "BW" or stripped == "PE":
        return True
    if (
        len(stripped) <= 12
        and _NOISE_TOKEN_RE.match(stripped)
        and any(ch.isdigit() for ch in stripped)
    ):
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
    if stripped.startswith("|") or stripped.endswith("|"):
        return True
    if stripped.count("|") >= 2:
        return True
    return False


def _has_table_leadin(out_lines: list[str]) -> bool:
    for line in reversed(out_lines):
        if not line.strip():
            continue
        return _is_sentence_line(line)
    return False


def _is_sentence_line(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("*"):
        return False
    if _looks_like_table_row(stripped):
        return False
    if stripped.endswith((".", "!", "?", ":")):
        return True
    return False


def _remove_repeated_lines(lines: list[str]) -> list[str]:
    normalized = [_normalize_line(line) for line in lines]
    counts: dict[str, int] = {}
    for line, norm in zip(lines, normalized):
        if not _line_is_repeat_candidate(line):
            continue
        counts[norm] = counts.get(norm, 0) + 1

    remove_set = {line for line, count in counts.items() if count >= 3}
    cleaned: list[str] = []
    for line, norm in zip(lines, normalized):
        if norm in remove_set and _line_is_repeat_candidate(line):
            continue
        cleaned.append(line)
    return cleaned


def _line_is_repeat_candidate(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("*"):
        return False
    if _looks_like_table_row(stripped) or _looks_like_table_separator(stripped):
        return False
    if stripped.startswith("```"):
        return False
    if len(stripped) > 80:
        return False
    if _SENTENCE_END_RE.search(stripped):
        return False
    return True


def _normalize_line(line: str) -> str:
    return " ".join(line.strip().split())


def _is_block_line(line: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return True
    if stripped.startswith(("- ", "* ", "+ ")):
        return True
    if re.match(r"^\d+\.\s+", stripped):
        return True
    if _looks_like_table_row(stripped) or _looks_like_table_separator(stripped):
        return True
    if stripped.startswith(">"):
        return True
    return False


def _should_merge_paragraph(current: str, incoming: str) -> bool:
    if not _SENTENCE_END_RE.search(current.strip()):
        return True
    if len(current.strip()) < 80:
        return True
    if len(incoming.strip()) < 40:
        return True
    return False
