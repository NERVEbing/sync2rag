from sync2rag.normalized_markdown import normalize_markdown


def test_image_ref_replaced_and_figures_appended() -> None:
    raw = (
        "Intro line.\n"
        "[ImageRef: FIG-abc123]\n\n"
        "## Images (auto-caption)\n"
        "### FIG-abc123\n"
        "![Alt text](https://example.com/image.png)\n"
        "Caption: Example caption.\n"
    )
    image_index = [{"figure_id": "FIG-abc123", "caption": "Example caption."}]
    normalized = normalize_markdown(raw, image_index)

    assert "ImageRef" not in normalized
    assert "FIG-abc123" not in normalized
    assert "(See figure: Example caption.)" in normalized
    assert "## Figures" in normalized
    assert "Figure 1: Example caption." in normalized


def test_image_ref_without_caption_is_removed() -> None:
    raw = "[ImageRef: FIG-abc123]\n"
    normalized = normalize_markdown(raw, [])
    assert "ImageRef" not in normalized
    assert "FIG-abc123" not in normalized
    assert "## Figures" not in normalized


def test_noise_lines_removed() -> None:
    raw = "A17\nI0\nPE\nThis is a sentence.\n2483.5 MHz\nBW\n"
    normalized = normalize_markdown(raw, [])
    assert "A17" not in normalized
    assert "I0" not in normalized
    assert "PE" not in normalized
    assert "2483.5 MHz" not in normalized
    assert "BW" not in normalized
    assert "This is a sentence." in normalized


def test_table_has_leadin_sentence() -> None:
    raw = "| Item | Value |\n| --- | --- |\n| A | 1 |\n"
    normalized = normalize_markdown(raw, [])
    assert "The following table summarizes the test results." in normalized
    assert "| Item | Value |" in normalized
