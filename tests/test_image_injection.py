"""Tests for inline image injection in normalized_markdown."""
from unittest.mock import patch

import pytest

from sync2rag.normalized_markdown import IMAGE_CAPTION_PREFIX, inject_images_inline, normalize_markdown


class TestInjectImagesInline:
    """Tests for inject_images_inline function."""

    def test_basic_single_placeholder(self) -> None:
        md_text = "Intro line.\n\n(See figure [FIG-001]: Example caption)\n\nEnd."
        image_index = [
            {
                "figure_id": "FIG-001",
                "image_public_url": "http://example.com/img1.png",
                "caption": "Example caption",
            }
        ]

        result = inject_images_inline(md_text, image_index)

        assert "![Example caption](http://example.com/img1.png)" in result
        assert f"{IMAGE_CAPTION_PREFIX} Example caption" in result
        lines = result.splitlines()
        img_idx = next(i for i, ln in enumerate(lines) if "![Example caption]" in ln)
        caption_idx = next(i for i, ln in enumerate(lines) if IMAGE_CAPTION_PREFIX in ln)
        assert img_idx < caption_idx

    def test_multiple_placeholders_order_match(self) -> None:
        md_text = (
            "(See figure [FIG-001]: First)\n"
            "Some text.\n"
            "(See figure [FIG-002]: Second)\n"
        )
        image_index = [
            {"figure_id": "FIG-001", "image_public_url": "http://example.com/1.png", "caption": "First"},
            {"figure_id": "FIG-002", "image_public_url": "http://example.com/2.png", "caption": "Second"},
        ]

        result = inject_images_inline(md_text, image_index)

        lines = result.splitlines()
        # First image and caption
        first_img_idx = next(i for i, ln in enumerate(lines) if "![First]" in ln)
        first_caption_idx = next(i for i, ln in enumerate(lines) if f"{IMAGE_CAPTION_PREFIX} First" in ln)
        # Second image and caption
        second_img_idx = next(i for i, ln in enumerate(lines) if "![Second]" in ln)
        second_caption_idx = next(i for i, ln in enumerate(lines) if f"{IMAGE_CAPTION_PREFIX} Second" in ln)

        assert first_img_idx < first_caption_idx < second_img_idx < second_caption_idx

    def test_more_placeholders_than_images_warns(self) -> None:
        md_text = "(See figure [FIG-001]: A)\n(See figure [FIG-002]: B)\n(See figure [FIG-003]: C)\n"
        image_index = [{"figure_id": "FIG-001", "image_public_url": "http://example.com/only.png", "caption": "A"}]

        with patch("sync2rag.normalized_markdown.logging") as mock_log:
            mock_logger = mock_log.getLogger.return_value
            result = inject_images_inline(md_text, image_index)
            assert mock_logger.warning.called
            warning_calls = [c[0][0] for c in mock_logger.warning.call_args_list]
            assert any("No matching figure_id" in w for w in warning_calls)

        assert "![A](http://example.com/only.png)" in result
        assert "![B]" not in result
        assert "![C]" not in result

    def test_more_images_than_placeholders_warns(self) -> None:
        md_text = "(See figure [FIG-001]: Only one)\n"
        image_index = [
            {"figure_id": "FIG-001", "image_public_url": "http://example.com/1.png", "caption": "Only one"},
            {"figure_id": "FIG-002", "image_public_url": "http://example.com/2.png", "caption": "Extra one"},
            {"figure_id": "FIG-003", "image_public_url": "http://example.com/3.png", "caption": "Another extra"},
        ]

        with patch("sync2rag.normalized_markdown.logging") as mock_log:
            mock_logger = mock_log.getLogger.return_value
            result = inject_images_inline(md_text, image_index)
            assert mock_logger.warning.called
            warning_calls = [c[0][0] for c in mock_logger.warning.call_args_list]
            assert any("matched to placeholders" in w for w in warning_calls)

        assert "![Only one](http://example.com/1.png)" in result

    def test_url_encoding_spaces(self) -> None:
        md_text = "(See figure [FIG-001]: Spaced name)\n"
        image_index = [
            {
                "figure_id": "FIG-001",
                "image_public_url": "http://example.com/path/file name.png",
                "caption": "Spaced name",
            }
        ]

        result = inject_images_inline(md_text, image_index)

        assert "file%20name.png" in result
        assert "file name.png" not in result

    def test_url_encoding_special_chars(self) -> None:
        md_text = "(See figure [FIG-001]: Special)\n"
        image_index = [
            {
                "figure_id": "FIG-001",
                "image_public_url": "http://example.com/path/(test)[1].png",
                "caption": "Special",
            }
        ]

        result = inject_images_inline(md_text, image_index)

        assert "%28test%29%5B1%5D.png" in result

    def test_url_encoding_no_double_encode(self) -> None:
        """Verify that already-encoded URLs are not double-encoded."""
        md_text = "(See figure [FIG-001]: Already encoded)\n"
        image_index = [
            {
                "figure_id": "FIG-001",
                "image_public_url": "http://example.com/path/file%20name.png",
                "caption": "Already encoded",
            }
        ]

        result = inject_images_inline(md_text, image_index)

        # Should remain %20, not become %2520
        assert "file%20name.png" in result
        assert "%2520" not in result

    def test_empty_image_index(self) -> None:
        md_text = "(See figure [FIG-001]: Orphan)\n"

        result = inject_images_inline(md_text, [])

        assert "![" not in result
        # When no matching image, the line with placeholder is not output (since matches is found but image_data is None)
        # The placeholder line is consumed by the match but nothing is appended since no image data
        assert "Orphan" not in result or "(See figure [FIG-001]: Orphan)" not in result

    def test_no_placeholders(self) -> None:
        md_text = "Just plain text without any figures.\n"
        image_index = [
            {"figure_id": "FIG-001", "image_public_url": "http://example.com/unused.png", "caption": "Unused"}
        ]

        with patch("sync2rag.normalized_markdown.logging") as mock_log:
            mock_logger = mock_log.getLogger.return_value
            result = inject_images_inline(md_text, image_index)
            # Should warn about unmatched images
            assert mock_logger.warning.called

        assert "![" not in result
        assert md_text.strip() == result.strip()

    def test_title_used_as_alt_text(self) -> None:
        """Verify that title is used for alt text when available."""
        md_text = "(See figure [FIG-001]: Full caption text here)\n"
        image_index = [
            {
                "figure_id": "FIG-001",
                "image_public_url": "http://example.com/img.png",
                "caption": "Full caption text here",
                "title": "Short Title",
            }
        ]

        result = inject_images_inline(md_text, image_index)

        assert "![Short Title](http://example.com/img.png)" in result
        assert f"{IMAGE_CAPTION_PREFIX} Full caption text here" in result

    def test_caption_truncated_without_title(self) -> None:
        """Verify that caption[:20] is used as alt when no title."""
        md_text = "(See figure [FIG-001]: This is a very long caption that exceeds twenty characters)\n"
        image_index = [
            {
                "figure_id": "FIG-001",
                "image_public_url": "http://example.com/img.png",
                "caption": "This is a very long caption that exceeds twenty characters",
            }
        ]

        result = inject_images_inline(md_text, image_index)

        # Alt text should be caption[:20] = "This is a very long "
        assert "![This is a very long ](http://example.com/img.png)" in result
        assert f"{IMAGE_CAPTION_PREFIX} This is a very long caption that exceeds twenty characters" in result

    def test_duplicate_figure_ids_handled(self) -> None:
        """Test that same figure_id appearing twice uses same image."""
        md_text = "(See figure [FIG-001]: Caption A)\nText.\n(See figure [FIG-001]: Caption A)\n"
        image_index = [
            {
                "figure_id": "FIG-001",
                "image_public_url": "http://example.com/img.png",
                "caption": "Caption A",
            }
        ]

        result = inject_images_inline(md_text, image_index)

        # Both placeholders should be replaced
        assert result.count("![Caption A](http://example.com/img.png)") == 2


class TestNormalizeMarkdownWithInjection:
    """Integration tests for normalize_markdown with image injection."""

    def test_full_flow_with_injection(self) -> None:
        raw = (
            "[ImageRef: FIG-001]\n"
            "Some text here.\n"
            "## Images (auto-caption)\n"
            "### FIG-001\n"
            "![Alt](http://old.url/img.png)\n"
            "Caption: Test caption.\n"
        )
        image_index = [
            {
                "figure_id": "FIG-001",
                "caption": "Test caption.",
                "image_public_url": "http://new.url/img.png",
            }
        ]

        result = normalize_markdown(raw, image_index)

        # Should have inline image with caption
        assert "![Test caption.](http://new.url/img.png)" in result
        assert f"{IMAGE_CAPTION_PREFIX} Test caption." in result
        # Old image section should be stripped
        assert "## Images" not in result
        assert "Caption:" not in result
