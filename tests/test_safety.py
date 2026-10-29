from jarvis.safety import ToolExecutor
from jarvis.tools.registry import Param, Risk, ToolRegistry, ToolResult


class ConfirmSpy:
    """Записывает, о чём спросили пользователя, и отвечает заранее заданным ответом."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return self.answer


def make_registry(calls: list) -> ToolRegistry:
    registry = ToolRegistry()

    @registry.tool(description="Громкость", params={"percent": Param("integer", "0–100")})
    def set_volume(percent: int) -> ToolResult:
        calls.append(("set_volume", percent))
        return ToolResult(ok=True, speech=f"Громкость {percent}")

    @registry.tool(
        description="Закрыть приложение",
        params={"app": Param("string", "Название")},
        risk=Risk.CONFIRM,
        confirm="Закрыть {app}?",
    )
    def close_app(app: str) -> ToolResult:
        calls.append(("close_app", app))
        return ToolResult(ok=True)

    @registry.tool(description="Ломается")
    def broken() -> ToolResult:
        raise OSError("устройство не найдено")

    return registry


def test_safe_tool_runs_without_confirmation():
    calls: list = []
    confirm = ConfirmSpy(answer=False)
    result = ToolExecutor(make_registry(calls), confirm).execute("set_volume", {"percent": 40})
    assert result.ok
    assert calls == [("set_volume", 40)]
    assert confirm.prompts == []


def test_risky_tool_asks_with_its_arguments_and_runs_after_yes():
    calls: list = []
    confirm = ConfirmSpy(answer=True)
    result = ToolExecutor(make_registry(calls), confirm).execute("close_app", {"app": "Telegram"})
    assert confirm.prompts == ["Закрыть Telegram?"]
    assert result.ok
    assert calls == [("close_app", "Telegram")]


def test_risky_tool_is_not_run_after_no():
    calls: list = []
    result = ToolExecutor(make_registry(calls), ConfirmSpy(answer=False)).execute("close_app", {"app": "Telegram"})
    assert not result.ok
    assert result.speech == "Отменено."
    assert calls == []


def test_unknown_tool_returns_error_instead_of_raising():
    result = ToolExecutor(make_registry([]), ConfirmSpy(answer=True)).execute("format_disk", {})
    assert not result.ok
    assert "format_disk" in result.error


def test_missing_required_argument_is_an_error_and_tool_is_not_run():
    calls: list = []
    result = ToolExecutor(make_registry(calls), ConfirmSpy(answer=True)).execute("set_volume", {})
    assert not result.ok
    assert "percent" in result.error
    assert calls == []


def test_unexpected_arguments_are_dropped():
    calls: list = []
    result = ToolExecutor(make_registry(calls), ConfirmSpy(answer=True)).execute(
        "set_volume", {"percent": 30, "device": "speakers"}
    )
    assert result.ok
    assert calls == [("set_volume", 30)]


def test_numeric_string_is_coerced_to_integer():
    calls: list = []
    ToolExecutor(make_registry(calls), ConfirmSpy(answer=True)).execute("set_volume", {"percent": "55"})
    assert calls == [("set_volume", 55)]


def test_non_numeric_string_for_integer_is_an_error():
    calls: list = []
    result = ToolExecutor(make_registry(calls), ConfirmSpy(answer=True)).execute("set_volume", {"percent": "громко"})
    assert not result.ok
    assert calls == []


def test_tool_exception_becomes_error_result():
    result = ToolExecutor(make_registry([]), ConfirmSpy(answer=True)).execute("broken", {})
    assert not result.ok
    assert "устройство не найдено" in result.error


def test_dry_run_neither_runs_tools_nor_asks():
    calls: list = []
    confirm = ConfirmSpy(answer=True)
    result = ToolExecutor(make_registry(calls), confirm, dry_run=True).execute("close_app", {"app": "Telegram"})
    assert result.ok
    assert calls == []
    assert confirm.prompts == []


def test_dry_run_says_itself_that_nothing_happened():
    # Иначе модель получает «ok» и рапортует «Блокнот открыт», хотя ничего не открывалось.
    result = ToolExecutor(make_registry([]), ConfirmSpy(answer=True), dry_run=True).execute("set_volume", {"percent": 40})
    assert result.speech is not None
    assert "set_volume" in result.speech
