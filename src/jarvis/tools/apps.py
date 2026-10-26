"""Запуск приложений из меню «Пуск» и закрытие окон."""

import ctypes
import json
import os
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from jarvis.tools.matching import find_best
from jarvis.tools.registry import Param, ToolRegistry, ToolResult


@dataclass(frozen=True)
class AppEntry:
    name: str
    app_id: str


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str


class AppsBackend(Protocol):
    def start_apps(self) -> list[AppEntry]: ...
    def launch(self, app_id: str) -> None: ...
    def windows(self) -> list[Window]: ...
    def close(self, hwnd: int) -> None: ...


def register(registry: ToolRegistry, backend: AppsBackend, aliases: dict[str, str]) -> None:
    @registry.tool(
        description="Запустить приложение на компьютере по названию, например «Telegram», «Блокнот», «Chrome».",
        params={"app": Param("string", "Название приложения так, как его назвал пользователь")},
    )
    def open_app(app: str) -> ToolResult:
        entries = {entry.name: entry for entry in backend.start_apps()}
        name = find_best(app, list(entries), aliases)
        if name is None:
            return ToolResult(ok=False, error=f"Приложение «{app}» не найдено в меню «Пуск»")
        backend.launch(entries[name].app_id)
        return ToolResult(ok=True, data={"opened": name}, speech=f"Открываю {name}.")

    @registry.tool(
        description="Закрыть открытое окно приложения по названию. Несохранённые данные приложение спросит само.",
        params={"app": Param("string", "Название приложения или часть заголовка окна")},
    )
    def close_app(app: str) -> ToolResult:
        windows = {window.title: window for window in backend.windows()}
        title = find_best(app, list(windows), aliases)
        if title is None:
            return ToolResult(ok=False, error=f"Открытое окно «{app}» не найдено")
        backend.close(windows[title].hwnd)
        return ToolResult(ok=True, data={"closed": title}, speech=f"Закрываю {app}.")


class WindowsApps:
    """Настоящий backend: меню «Пуск» через Get-StartApps, окна через WinAPI."""

    _WM_CLOSE = 0x0010
    _IGNORED_TITLES = {"Program Manager"}

    def __init__(self) -> None:
        self._start_apps: list[AppEntry] | None = None

    def start_apps(self) -> list[AppEntry]:
        # Get-StartApps видит и обычные программы, и приложения из Microsoft Store.
        if self._start_apps is None:
            script = (
                "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
            )
            output = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
            items = json.loads(output)
            if isinstance(items, dict):
                items = [items]
            self._start_apps = [AppEntry(item["Name"], item["AppID"]) for item in items]
        return self._start_apps

    def launch(self, app_id: str) -> None:
        os.startfile(f"shell:AppsFolder\\{app_id}")

    def windows(self) -> list[Window]:
        user32 = ctypes.windll.user32
        found: list[Window] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def collect(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    if buffer.value not in self._IGNORED_TITLES:
                        found.append(Window(int(hwnd), buffer.value))
            return True

        user32.EnumWindows(collect, 0)
        return found

    def close(self, hwnd: int) -> None:
        # WM_CLOSE — как нажатие крестика: приложение само спросит про несохранённое.
        ctypes.windll.user32.PostMessageW(hwnd, self._WM_CLOSE, 0, 0)
