import pytest

from jarvis.tools.registry import Param, Risk, ToolRegistry, ToolResult


def test_schema_lists_required_and_optional_params():
    registry = ToolRegistry()

    @registry.tool(
        description="Открыть сайт",
        params={
            "url": Param("string", "Адрес сайта"),
            "new_window": Param("boolean", "В новом окне", required=False),
        },
    )
    def open_url(url: str, new_window: bool = False) -> ToolResult:
        return ToolResult(ok=True)

    assert registry.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "open_url",
                "description": "Открыть сайт",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Адрес сайта"},
                        "new_window": {"type": "boolean", "description": "В новом окне"},
                    },
                    "required": ["url"],
                },
            },
        }
    ]


def test_enum_param_lists_allowed_values():
    registry = ToolRegistry()

    @registry.tool(
        description="Управление плеером",
        params={"action": Param("string", "Действие", enum=["play_pause", "next"])},
    )
    def media_control(action: str) -> ToolResult:
        return ToolResult(ok=True)

    action = registry.schemas()[0]["function"]["parameters"]["properties"]["action"]
    assert action == {"type": "string", "description": "Действие", "enum": ["play_pause", "next"]}


def test_registered_tool_keeps_risk_and_runs():
    registry = ToolRegistry()

    @registry.tool(description="Закрыть приложение", risk=Risk.CONFIRM, confirm="Закрыть {app}?")
    def close_app(app: str) -> ToolResult:
        return ToolResult(ok=True, speech=f"Закрываю {app}")

    tool = registry.get("close_app")
    assert tool.risk is Risk.CONFIRM
    assert tool.confirm == "Закрыть {app}?"
    assert tool.func(app="Telegram").speech == "Закрываю Telegram"


def test_tool_without_risk_is_safe():
    registry = ToolRegistry()

    @registry.tool(description="Который час")
    def get_time() -> ToolResult:
        return ToolResult(ok=True)

    assert registry.get("get_time").risk is Risk.SAFE


def test_duplicate_name_is_rejected():
    registry = ToolRegistry()

    @registry.tool(description="Первый")
    def ping() -> ToolResult:
        return ToolResult(ok=True)

    with pytest.raises(ValueError, match="ping"):

        @registry.tool(description="Второй")
        def ping() -> ToolResult:  # noqa: F811
            return ToolResult(ok=True)


def test_unknown_tool_lookup_returns_none():
    assert ToolRegistry().get("nope") is None


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ToolResult(ok=True, data={"volume": 50}), '{"ok": true, "data": {"volume": 50}}'),
        (ToolResult(ok=False, error="Нет такого приложения"), '{"ok": false, "error": "Нет такого приложения"}'),
    ],
)
def test_result_message_for_llm_keeps_cyrillic(result, message):
    assert result.to_message() == message
