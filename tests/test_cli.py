import pytest

from sync2rag.cli import _extract_config_arg


def test_extract_config_arg_long_form() -> None:
    cleaned, config_path = _extract_config_arg(["--config", "foo.yaml", "scan"])
    assert cleaned == ["scan"]
    assert config_path == "foo.yaml"


def test_extract_config_arg_short_form() -> None:
    cleaned, config_path = _extract_config_arg(["-c=bar.yaml", "run"])
    assert cleaned == ["run"]
    assert config_path == "bar.yaml"


def test_extract_config_arg_missing_value() -> None:
    with pytest.raises(ValueError):
        _extract_config_arg(["-c"])
