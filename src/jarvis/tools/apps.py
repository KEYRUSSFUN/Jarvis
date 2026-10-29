"""Запуск приложений из меню «Пуск» и закрытие окон."""

import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz import fuzz

from jarvis.tools import winapi
from jarvis.tools.files import SHORTCUT_SUFFIXES, FilesBackend, LaunchLog
from jarvis.tools.matching import find_best
from jarvis.tools.registry import Param, ToolRegistry, ToolResult
from jarvis.tools.winapi import Window

# Просьбы открыть «браузер вообще». Internet Explorer в Windows 11 удалён, и просят его,
# чтобы попасть в интернет, — такие запросы открывают браузер по умолчанию.
_ANY_BROWSER_WORDS = ("браузер", "browser", "интернет", "internet", "эксплорер", "explorer", "веб", "web")


def _wants_any_browser(app: str) -> bool:
    words = re.findall(r"\w+", app.lower())
    return bool(words) and all(max(fuzz.ratio(word, known) for known in _ANY_BROWSER_WORDS) >= 80 for word in words)


@dataclass(frozen=True)
class AppEntry:
    name: str
    app_id: str


class AppsBackend(Protocol):
    def start_apps(self) -> list[AppEntry]: ...
    def launch(self, app_id: str) -> None: ...
    def open_browser(self) -> None: ...
    def windows(self) -> list[Window]: ...
    def close(self, hwnd: int) -> None: ...


def register(
    registry: ToolRegistry,
    backend: AppsBackend,
    aliases: dict[str, str],
    *,
    files: FilesBackend | None = None,
    launched: LaunchLog | None = None,
    close_background: Callable[[str], ToolResult | None] | None = None,
) -> None:
    def mark_launched() -> None:
        if launched is not None:
            launched.mark()

    @registry.tool(
        description="Запустить приложение на компьютере по названию, например «Telegram», «Блокнот», «Chrome».",
        params={"app": Param("string", "Название приложения так, как его назвал пользователь")},
    )
    def open_app(app: str) -> ToolResult:
        entries = {entry.name: entry for entry in backend.start_apps()}
        name = find_best(app, list(entries), aliases)
        if name is not None:
            backend.launch(entries[name].app_id)
            mark_launched()
            return ToolResult(ok=True, data={"opened": name}, speech=f"Открываю {name}.")

        # Игры и портативные программы нет в «Пуске», но у них бывает ярлык на рабочем столе.
        if files is not None:
            shortcuts = {e.name: e for e in files.entries() if e.path.suffix.lower() in SHORTCUT_SUFFIXES}
            shortcut = find_best(app, list(shortcuts), aliases)
            if shortcut is not None:
                files.open(shortcuts[shortcut].path)
                mark_launched()
                return ToolResult(ok=True, data={"opened": shortcut}, speech=f"Открываю {shortcut}.")

        if _wants_any_browser(app):
            backend.open_browser()
            mark_launched()
            return ToolResult(ok=True, data={"opened": "браузер по умолчанию"}, speech="Открываю браузер.")
        return ToolResult(ok=False, error=f"Приложение «{app}» не найдено в меню «Пуск»")

    @registry.tool(
        description="Закрыть открытое окно приложения по названию. Несохранённые данные приложение спросит само.",
        params={"app": Param("string", "Название приложения или часть заголовка окна")},
    )
    def close_app(app: str) -> ToolResult:
        windows = {window.title: window for window in backend.windows()}
        title = find_best(app, list(windows), aliases)
        if title is None:
            # Программа может жить в трее без окна (Steam): пробуем закрыть её как процесс.
            background = close_background(app) if close_background is not None else None
            if background is not None:
                return background
            return ToolResult(ok=False, error=f"Открытое окно «{app}» не найдено")
        backend.close(windows[title].hwnd)
        return ToolResult(ok=True, data={"closed": title}, speech=f"Закрываю {app}.")


class WindowsApps:
    """Настоящий backend: меню «Пуск» через Get-StartApps, окна через WinAPI."""

    _HTTPS_CHOICE = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"

    def __init__(self) -> None:
        self._start_apps: list[AppEntry] | None = None
        self._lock = threading.Lock()

    def preload(self) -> None:
        """Читает меню «Пуск» в фоне (~1 с), чтобы первая команда «открой …» его не ждала."""
        threading.Thread(target=self.start_apps, name="start-apps", daemon=True).start()

    def start_apps(self) -> list[AppEntry]:
        # Get-StartApps видит и обычные программы, и приложения из Microsoft Store.
        # Под замком: команда во время предзагрузки дождётся её, а не запустит PowerShell второй раз.
        with self._lock:
            if self._start_apps is None:
                self._start_apps = self._read_start_apps()
        return self._start_apps

    @staticmethod
    def _read_start_apps() -> list[AppEntry]:
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
        return [AppEntry(item["Name"], item["AppID"]) for item in items]

    def launch(self, app_id: str) -> None:
        os.startfile(f"shell:AppsFolder\\{app_id}")

    def open_browser(self) -> None:
        # Браузер по умолчанию — тот, что открывает https-ссылки. Запускаем его exe без адреса,
        # как с ярлыка. Если ассоциации нет — Edge, он есть в любой Windows 10/11.
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._HTTPS_CHOICE) as key:
                prog_id = winreg.QueryValueEx(key, "ProgId")[0]
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
                command = winreg.QueryValueEx(key, "")[0]
        except OSError:
            command = ""
        # Команда вида: "C:\...\browser.exe" --single-argument %1
        exe = re.match(r'\s*(?:"([^"]+)"|(\S+))', command)
        os.startfile((exe.group(1) or exe.group(2)) if exe else "microsoft-edge:")

    def windows(self) -> list[Window]:
        return winapi.visible_windows()

    def close(self, hwnd: int) -> None:
        winapi.post_close(hwnd)
