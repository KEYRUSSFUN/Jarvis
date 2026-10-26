"""Реестр инструментов, которые может вызывать LLM.

Каждый инструмент объявляет уровень риска. Опасные (Risk.CONFIRM) выполняются
только после голосового подтверждения — это проверяет ToolExecutor, а не промпт.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Risk(Enum):
    SAFE = "safe"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class Param:
    type: str
    description: str
    required: bool = True
    enum: list[str] | None = None

    def schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum is not None:
            schema["enum"] = self.enum
        return schema


@dataclass
class ToolResult:
    """Результат инструмента.

    speech — готовая фраза для озвучки. Если она есть у всех вызовов шага,
    агент отвечает ею сразу, без второго запроса к LLM.
    """

    ok: bool
    data: Any = None
    error: str | None = None
    speech: str | None = None

    def to_message(self) -> str:
        message: dict[str, Any] = {"ok": self.ok}
        if self.data is not None:
            message["data"] = self.data
        if self.error is not None:
            message["error"] = self.error
        return json.dumps(message, ensure_ascii=False)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    func: Callable[..., ToolResult]
    params: dict[str, Param] = field(default_factory=dict)
    risk: Risk = Risk.SAFE
    confirm: str | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {name: param.schema() for name, param in self.params.items()},
                    "required": [name for name, param in self.params.items() if param.required],
                },
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def tool(
        self,
        description: str,
        params: dict[str, Param] | None = None,
        risk: Risk = Risk.SAFE,
        confirm: str | None = None,
    ) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
        def register(func: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
            if func.__name__ in self._tools:
                raise ValueError(f"Инструмент {func.__name__} уже зарегистрирован")
            self._tools[func.__name__] = Tool(
                name=func.__name__,
                description=description,
                func=func,
                params=params or {},
                risk=risk,
                confirm=confirm,
            )
            return func

        return register

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]
