"""Прогон набора команд через настоящую модель: uv run pytest -m llm

Проверяет связку «модель + системный промпт + описания инструментов»:
какой инструмент модель вызывает первым и с какими аргументами.
Инструменты не выполняются.
"""

from pathlib import Path

import pytest
import yaml

from jarvis.config import Config
from jarvis.llm import OllamaChat, ToolCall
from jarvis.prompts import SYSTEM_PROMPT
from jarvis.tools import build_registry
from jarvis.tools.matching import find_best

CASES = yaml.safe_load((Path(__file__).parent / "commands.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def llm() -> OllamaChat:
    config = Config().llm
    chat = OllamaChat(config.model, config.host, config.keep_alive, config.temperature)
    try:
        chat.warm_up()
    except Exception as exc:
        pytest.skip(f"Ollama недоступен: {exc}")
    return chat


@pytest.fixture(scope="module")
def schemas() -> list[dict]:
    return build_registry(Config()).schemas()


def arg_matches(actual, expected) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected
    if isinstance(expected, str) and expected[:1] in "<>" and isinstance(actual, int | float):
        return actual > 0 if expected == ">0" else actual < 0
    if isinstance(expected, str):
        # В обе стороны: «кто такой Тесла» содержит «Тесла», а «Visual Studio Code» — «VS Code».
        return isinstance(actual, str) and (
            find_best(actual, [expected]) is not None or find_best(expected, [actual]) is not None
        )
    return actual == expected


@pytest.mark.llm
@pytest.mark.parametrize("case", CASES, ids=[case["say"] for case in CASES])
def test_command(case, llm, schemas):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": case["say"]}]
    calls = [event for event in llm.stream(messages, schemas) if isinstance(event, ToolCall)]
    expect = case["expect"]

    if expect is None:
        assert calls == [], f"не ждали инструментов, а модель вызвала {calls}"
        return

    assert calls, "модель ответила текстом, а не вызовом инструмента"
    call = calls[0]
    assert call.name == expect["tool"], f"вызван {call.name}({call.args})"
    for name, expected in expect.get("args", {}).items():
        assert arg_matches(call.args.get(name), expected), f"{name}={call.args.get(name)!r}, ждали {expected!r}"
