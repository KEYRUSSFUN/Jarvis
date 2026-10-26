import copy
import json

from jarvis.agent import GAVE_UP, NOT_UNDERSTOOD, Agent
from jarvis.llm import TextDelta, ToolCall
from jarvis.safety import ToolExecutor
from jarvis.tools.registry import Param, ToolRegistry, ToolResult

SYSTEM = "Ты Jarvis."


class ScriptedLLM:
    """Отдаёт заранее заданные ответы по одному на вызов и запоминает, что ему прислали."""

    def __init__(self, *responses: list) -> None:
        self._responses = list(responses)
        self.requests: list[list[dict]] = []

    def stream(self, messages, tools):
        self.requests.append(copy.deepcopy(messages))
        if not self._responses:
            raise AssertionError("LLM вызвана больше раз, чем ожидалось")
        yield from self._responses.pop(0)


def make_agent(llm, max_steps: int = 5) -> tuple[Agent, list]:
    calls: list = []
    registry = ToolRegistry()

    @registry.tool(description="Открыть приложение", params={"app": Param("string", "Название")})
    def open_app(app: str) -> ToolResult:
        calls.append(("open_app", app))
        return ToolResult(ok=True, speech=f"Открываю {app}.")

    @registry.tool(description="Пауза или воспроизведение")
    def media_play_pause() -> ToolResult:
        calls.append(("media_play_pause",))
        return ToolResult(ok=True, speech="Готово.")

    @registry.tool(description="Загрузка системы")
    def system_info() -> ToolResult:
        calls.append(("system_info",))
        return ToolResult(ok=True, data={"cpu_percent": 12})

    executor = ToolExecutor(registry, confirm=lambda prompt: True)
    return Agent(llm, registry, executor, SYSTEM, max_steps=max_steps), calls


def run(agent: Agent, text: str) -> list[str]:
    spoken: list[str] = []
    agent.run(text, spoken.append)
    return spoken


def test_plain_answer_is_spoken_sentence_by_sentence():
    llm = ScriptedLLM([TextDelta("Привет. Я Jar"), TextDelta("vis, к вашим услугам.")])
    agent, _ = make_agent(llm)
    assert run(agent, "кто ты") == ["Привет.", "Я Jarvis, к вашим услугам."]


def test_first_request_has_system_prompt_and_user_text():
    llm = ScriptedLLM([TextDelta("Да.")])
    agent, _ = make_agent(llm)
    run(agent, "ты тут?")
    assert llm.requests[0] == [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "ты тут?"},
    ]


def test_tool_speech_answers_without_second_llm_call():
    llm = ScriptedLLM([ToolCall("open_app", {"app": "Telegram"})])
    agent, calls = make_agent(llm)
    assert run(agent, "открой телеграм") == ["Открываю Telegram."]
    assert calls == [("open_app", "Telegram")]
    assert len(llm.requests) == 1


def test_parallel_calls_with_speech_are_all_spoken():
    llm = ScriptedLLM([ToolCall("open_app", {"app": "Spotify"}), ToolCall("media_play_pause", {})])
    agent, calls = make_agent(llm)
    assert run(agent, "открой спотифай и включи музыку") == ["Открываю Spotify.", "Готово."]
    assert calls == [("open_app", "Spotify"), ("media_play_pause",)]


def test_tool_data_goes_back_to_llm_for_the_answer():
    llm = ScriptedLLM(
        [ToolCall("system_info", {})],
        [TextDelta("Процессор загружен на 12%.")],
    )
    agent, _ = make_agent(llm)
    assert run(agent, "как загружен процессор") == ["Процессор загружен на 12%."]

    second = llm.requests[1]
    assert second[2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "system_info", "arguments": {}}}],
    }
    assert second[3]["role"] == "tool"
    assert second[3]["tool_name"] == "system_info"
    assert json.loads(second[3]["content"]) == {"ok": True, "data": {"cpu_percent": 12}}


def test_tool_speech_is_not_spoken_when_llm_has_to_continue():
    llm = ScriptedLLM(
        [ToolCall("open_app", {"app": "Chrome"}), ToolCall("system_info", {})],
        [TextDelta("Открыл Chrome, процессор свободен.")],
    )
    agent, _ = make_agent(llm)
    assert run(agent, "открой хром и скажи загрузку") == ["Открыл Chrome, процессор свободен."]


def test_failed_tool_without_speech_lets_llm_explain():
    llm = ScriptedLLM(
        [ToolCall("delete_everything", {})],
        [TextDelta("Такого я не умею.")],
    )
    agent, _ = make_agent(llm)
    assert run(agent, "удали всё") == ["Такого я не умею."]
    error = json.loads(llm.requests[1][3]["content"])
    assert error["ok"] is False


def test_gives_up_after_max_steps():
    llm = ScriptedLLM(*([ToolCall("system_info", {})] for _ in range(3)))
    agent, calls = make_agent(llm, max_steps=3)
    assert run(agent, "зациклись") == [GAVE_UP]
    assert len(llm.requests) == 3
    assert len(calls) == 3


def test_empty_answer_says_not_understood():
    agent, _ = make_agent(ScriptedLLM([]))
    assert run(agent, "эээ") == [NOT_UNDERSTOOD]
