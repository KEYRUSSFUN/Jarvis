"""UI Automation: клики по кнопкам и элементам внутри окон программ.

Использует Microsoft UI Automation через comtypes (уже в зависимостях).
Позволяет нажимать кнопки, выбирать элементы меню и другие элементы управления
в окнах любых программ, а не только в браузере.
"""

import logging

import comtypes.client  # type: ignore

from jarvis.tools import winapi
from jarvis.tools.matching import find_best
from jarvis.tools.registry import Param, Risk, ToolRegistry, ToolResult

log = logging.getLogger(__name__)

# CLSID для объекта Microsoft UI Automation.
_CLSID_UIAUTOMATION = "{FF48DBA4-60F3-403B-A798-7F6D6E6E6E6E}"

# Идентификаторы свойств и паттернов UI Automation (стандартные значения Windows).
_UIA_NAME_PROPERTY_ID = 30000
_UIA_INVOKE_PATTERN_ID = 10000
_TREE_SCOPE_DESCENDANTS = 0x20000


def _get_automation():
    """Создаёт объект IUIAutomation."""
    return comtypes.client.CreateObject(_CLSID_UIAUTOMATION, interface="IUIAutomation")


def _element_by_name(root, name: str):
    """Ищет descendant-элемент с заданным Name (NameProperty)."""
    condition = root.CreatePropertyCondition(_UIA_NAME_PROPERTY_ID, name)
    return root.FindFirst(_TREE_SCOPE_DESCENDANTS, condition)


def _find_window_hwnd(window: str) -> int | None:
    windows = winapi.visible_windows()
    titles = [w.title for w in windows]
    match = find_best(window, titles)
    if match is None:
        return None
    for w in windows:
        if w.title == match:
            return w.hwnd
    return None


def _collect_names(element, depth: int = 0, max_depth: int = 3) -> list[str]:
    """Рекурсивно собирает имена элементов управления (для отладки)."""
    names: list[str] = []
    if depth > max_depth:
        return names
    try:
        name = element.CurrentName
        if name:
            names.append(name)
    except Exception:
        pass
    try:
        children = element.FindAll(_TREE_SCOPE_DESCENDANTS, None)
        for child in children:
            names.extend(_collect_names(child, depth + 1, max_depth))
    except Exception:
        pass
    return names


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        description="Кликнуть по кнопке или элементу управления в окне программы по названию.",
        params={
            "window": Param("string", "Заголовок окна, например «Блокнот» или «Телеграм»"),
            "control": Param("string", "Название кнопки или элемента, например «Сохранить» или «Отправить»"),
        },
        risk=Risk.SAFE,
    )
    def click_control(window: str, control: str) -> ToolResult:
        hwnd = _find_window_hwnd(window)
        if hwnd is None:
            return ToolResult(ok=False, error=f"Окно «{window}» не найдено")
        try:
            automation = _get_automation()
            root = automation.ElementFromHandle(hwnd)
            element = _element_by_name(root, control)
            if element is None:
                return ToolResult(ok=False, error=f"Элемент «{control}» не найден в окне «{window}»")
            pattern = element.GetCurrentPattern(_UIA_INVOKE_PATTERN_ID)
            pattern.Invoke()
        except Exception as exc:
            log.exception("UI Automation клик не удался: %s", control)
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(ok=True, speech=f"Нажал {control} в {window}.")

    @registry.tool(
        description="Показать список кнопок и элементов в окне программы (для подбора точных названий).",
        params={"window": Param("string", "Заголовок окна")},
        risk=Risk.SAFE,
    )
    def list_controls(window: str) -> ToolResult:
        hwnd = _find_window_hwnd(window)
        if hwnd is None:
            return ToolResult(ok=False, error=f"Окно «{window}» не найдено")
        try:
            automation = _get_automation()
            root = automation.ElementFromHandle(hwnd)
            names = _collect_names(root)
        except Exception as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        if not names:
            return ToolResult(ok=False, error="Элементы не найдены")
        speech = "Элементы: " + ", ".join(names[:10])
        return ToolResult(ok=True, data={"controls": names}, speech=speech)