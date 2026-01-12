import textwrap
from pathlib import Path

import pytest

from sync2rag.config import ConfigError, _as_list, _normalize_exts, load_config


def test_as_list_normalizes_values() -> None:
    assert _as_list(None) == []
    assert _as_list("pdf") == ["pdf"]
    assert _as_list([1, "a"]) == ["1", "a"]


def test_normalize_exts() -> None:
    assert _normalize_exts(["PDF", ".Docx", " ", ""]) == [".pdf", ".docx"]


def test_load_config_requires_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfigError):
        load_config(missing)


def test_load_config_minimal(tmp_path: Path) -> None:
    root_dir = tmp_path / "input"
    root_dir.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            input:
              root_dir: {root_dir.as_posix()}
            docling:
              base_url: http://localhost:5001
            """
        ).lstrip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.input.root_dir == root_dir
    assert config.docling.base_url == "http://localhost:5001"
    assert config.output.root_dir == Path("data")
