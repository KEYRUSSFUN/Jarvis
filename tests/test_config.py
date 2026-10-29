from pathlib import Path

import pytest

from jarvis.config import Config, LLMConfig, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_file_overrides_only_given_keys(tmp_path):
    config = load_config(
        write(
            tmp_path,
            """
            [llm]
            model = "qwen3:14b"

            [apps.aliases]
            "телега" = "Telegram"
            """,
        )
    )
    assert config.llm.model == "qwen3:14b"
    assert config.llm.host == LLMConfig().host
    assert config.apps.aliases == {"телега": "Telegram"}


def test_unknown_key_is_reported(tmp_path):
    with pytest.raises(ValueError, match="modle"):
        load_config(write(tmp_path, '[llm]\nmodle = "qwen3:8b"\n'))


def test_unknown_section_is_reported(tmp_path):
    with pytest.raises(ValueError, match="sst"):
        load_config(write(tmp_path, '[sst]\nmodel = "small"\n'))
