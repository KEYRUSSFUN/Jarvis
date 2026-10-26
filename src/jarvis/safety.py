"""Слой безопасности: единственная дорога от LLM к инструментам.

Проверяет имя и аргументы вызова, спрашивает подтверждение для опасных
инструментов и превращает любые ошибки в ToolResult, чтобы агент мог
сообщить о них, а не упасть.
"""

import logging
from collections.abc import Callable
from typing import Any

from jarvis.tools.registry import Param, Risk, Tool, ToolRegistry, ToolResult

log = logging.getLogger(__name__)

Confirm = Callable[[str], bool]


def _coerce(value: Any, param: Param) -> Any:
    if not isinstance(value, str):
        return value
    if param.type == "integer":
        return int(value)
    if param.type == "number":
        return float(value.replace(",", "."))
    if param.type == "boolean":
        return value.strip().lower() in ("true", "1", "да")
    return value


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, confirm: Confirm, dry_run: bool = False) -> None:
        self._registry = registry
        self._confirm = confirm
        self._dry_run = dry_run

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._registry.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"Неизвестный инструмент: {name}")

        try:
            kwargs = self._prepare_args(tool, args)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))

        if self._dry_run:
            log.info("dry-run: %s(%s)", name, kwargs)
            return ToolResult(
                ok=True,
                data="dry-run: инструмент не выполнялся",
                speech=f"Тестовый режим: {name} не выполнялся.",
            )

        if tool.risk is Risk.CONFIRM:
            prompt = (tool.confirm or f"Выполнить {name}?").format(**kwargs)
            if not self._confirm(prompt):
                log.info("Отменено пользователем: %s(%s)", name, kwargs)
                return ToolResult(ok=False, error="Пользователь отменил действие", speech="Отменено.")

        try:
            return tool.func(**kwargs)
        except Exception as exc:
            log.exception("Инструмент %s упал", name)
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _prepare_args(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
        unexpected = set(args) - set(tool.params)
        if unexpected:
            log.warning("%s: лишние аргументы отброшены: %s", tool.name, sorted(unexpected))

        kwargs = {}
        for param_name, param in tool.params.items():
            if param_name not in args:
                if param.required:
                    raise ValueError(f"Не хватает аргумента {param_name}")
                continue
            try:
                kwargs[param_name] = _coerce(args[param_name], param)
            except ValueError:
                raise ValueError(f"Аргумент {param_name} должен быть типа {param.type}") from None
        return kwargs
