"""Печать текста и сочетания клавиш в выбранном окне."""

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from jarvis.tools import winapi
from jarvis.tools.files import LaunchLog
from jarvis.tools.matching import find_best
from jarvis.tools.registry import Param, ToolRegistry, ToolResult
from jarvis.tools.winapi import Window

# Окно только что запущенной программы появляется не сразу: ждём его, пока запуск «свежий».
WINDOW_WAIT_SECONDS = 15
RECENT_LAUNCH_SECONDS = 20
_POLL_SECONDS = 0.1

_KEY_CODES: dict[str, int] = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
    **{chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)},  # VK букв = коды заглавных
    **{str(n): 0x30 + n for n in range(10)},
}  # fmt: skip
_SYNONYMS = {"control": "ctrl", "return": "enter", "escape": "esc", "del": "delete"}


def parse_keys(keys: str) -> list[int]:
    """«Ctrl + Shift + T» → коды виртуальных клавиш в порядке нажатия."""
    names = [part for part in re.split(r"[+\s]+", keys.strip().lower()) if part]
    codes: list[int] = []
    for name in names:
        code = _KEY_CODES.get(_SYNONYMS.get(name, name))
        if code is None:
            raise ValueError(f"Неизвестная клавиша «{name}». Допустимые: {', '.join(_KEY_CODES)}")
        codes.append(code)
    if not codes:
        raise ValueError(f"Не указаны клавиши. Допустимые: {', '.join(_KEY_CODES)}")
    return codes


class InputBackend(Protocol):
    def windows(self) -> list[Window]: ...
    def activate(self, hwnd: int) -> bool: ...
    # target — окно, в которое печатаем; None — активное в момент начала печати.
    # Сменилось окно или человек зажал Ctrl, Alt, Win — winapi.InputInterrupted.
    def type_text(self, text: str, target: int | None) -> None: ...
    def press(self, vks: list[int]) -> None: ...


def register(
    registry: ToolRegistry,
    backend: InputBackend,
    launched: LaunchLog,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    def find_window(name: str) -> Window | None:
        deadline = clock() + WINDOW_WAIT_SECONDS
        while True:
            windows: dict[str, Window] = {}
            for window in backend.windows():
                # Окна идут сверху вниз: из окон с одинаковым заголовком берём верхнее.
                windows.setdefault(window.title, window)
            titles = list(windows)
            # «Отчёт.docx» в заголовке окна обычно без расширения — пробуем и без него.
            title = find_best(name, titles) or find_best(Path(name).stem, titles)
            if title is not None:
                return windows[title]
            if not launched.recent(RECENT_LAUNCH_SECONDS) or clock() >= deadline:
                return None
            sleep(_POLL_SECONDS)

    def focus(name: str | None) -> ToolResult | int | None:
        """Выводит нужное окно вперёд: его hwnd, ошибка или None, если окно не задано (вводим в активное)."""
        if not name:
            return None
        window = find_window(name)
        if window is None:
            return ToolResult(ok=False, error=f"Открытое окно «{name}» не найдено")
        if not backend.activate(window.hwnd):
            return ToolResult(ok=False, error=f"Не удалось вывести окно «{window.title}» на передний план")
        return window.hwnd

    def send(name: str | None, action: Callable[[int | None], None], speech: str, stopped: str) -> ToolResult:
        target = focus(name)
        if isinstance(target, ToolResult):
            return target
        try:
            action(target)
        except winapi.InputInterrupted as exc:
            # С готовой фразой агент сразу её скажет и не даст модели второй ход — иначе она напечатала бы всё заново.
            return ToolResult(ok=False, error=str(exc), speech=f"{stopped}: {exc.reason}.")
        except OSError as exc:
            return ToolResult(
                ok=False,
                error=f"{exc}. Возможно, окно запущено от имени администратора, а Jarvis — нет",
            )
        return ToolResult(ok=True, speech=speech)

    @registry.tool(
        description=(
            "Напечатать текст в окне. Если окно не указано, текст уходит в активное окно. "
            "Переводы строк в тексте превращаются в нажатия Enter."
        ),
        params={
            "text": Param("string", "Текст, который нужно напечатать"),
            "window": Param(
                "string", "Название окна или документа, например «Блокнот» или «Отчёт»", required=False
            ),
        },
    )
    def type_text(text: str, window: str | None = None) -> ToolResult:
        return send(window, lambda target: backend.type_text(text, target), "Напечатал.", "Остановил печать")

    @registry.tool(
        description=(
            "Нажать клавишу или сочетание клавиш, например «ctrl+s», «alt+f4», «enter», «ctrl+shift+t». "
            "Подходит для просьб «Сохрани» (ctrl+s), «Нажми энтер» (enter), «Отмени» (ctrl+z). "
            "Если окно не указано, нажатие уходит в активное окно."
        ),
        params={
            "keys": Param("string", "Клавиши через «+»: ctrl, shift, alt, win, enter, tab, esc, f1-f12, a-z, 0-9"),
            "window": Param("string", "Название окна, в котором нажать клавиши", required=False),
        },
    )
    def press_keys(keys: str, window: str | None = None) -> ToolResult:
        try:
            vks = parse_keys(keys)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return send(window, lambda target: backend.press(vks), "Готово.", "Не стал нажимать")


class WindowsInput:
    """Настоящий backend: окна и ввод через WinAPI."""

    def windows(self) -> list[Window]:
        return winapi.visible_windows()

    def activate(self, hwnd: int) -> bool:
        return winapi.activate(hwnd)

    def type_text(self, text: str, target: int | None) -> None:
        winapi.send_text(text, target)

    def press(self, vks: list[int]) -> None:
        winapi.send_keys(vks)
