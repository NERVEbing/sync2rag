from sync2rag.scanner import _is_bad_caption, _normalize_caption_text


def test_normalize_caption_strips_leading_fillers() -> None:
    raw = "好的，这张图片是一个标志"
    cleaned = _normalize_caption_text(raw)
    assert "好的" not in cleaned
    assert cleaned.startswith("这张图片")


def test_bad_caption_rejects_chinese_refusals() -> None:
    assert _is_bad_caption("无法看到图片") is True
    assert _is_bad_caption("请上传图片") is True


def test_bad_caption_rejects_trivial_chinese_words() -> None:
    assert _is_bad_caption("图片") is True
