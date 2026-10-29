"""Поиск и открытие файлов, папок и ярлыков с рабочего стола, из «Документов» и «Загрузок»."""

import ctypes
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jarvis.tools.matching import find_best
from jarvis.tools.registry import Param, ToolRegistry, ToolResult

SHORTCUT_SUFFIXES = {".lnk", ".url"}

# Скрытый и системный файлы: FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM.
_HIDDEN_OR_SYSTEM = 0x6

_FOLDER_IDS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "PublicDesktop": "{C4AA340D-F20F-4863-AFEF-F87EF2E6BA25}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}


@dataclass(frozen=True)
class FileEntry:
    name: str  # без расширения у файлов и ярлыков, полное имя у папок
    path: Path


class FilesBackend(Protocol):
    def entries(self) -> list[FileEntry]: ...
    def open(self, path: Path) -> None: ...


class LaunchLog:
    """Помнит, когда Jarvis последний раз что-то запускал: новое окно ещё не успело появиться."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: float | None = None

    def mark(self) -> None:
        self._last = self._clock()

    def recent(self, seconds: float) -> bool:
        return self._last is not None and self._clock() - self._last <= seconds


def _visible(path: Path) -> bool:
    if path.name.startswith(".") or path.name.lower() == "desktop.ini":
        return False
    try:
        return not path.stat().st_file_attributes & _HIDDEN_OR_SYSTEM
    except OSError:
        return False


def _entry(path: Path) -> FileEntry:
    return FileEntry(path.name if path.is_dir() else path.stem, path)


def scan(folders: list[Path], depth: int = 1, limit: int = 3000) -> list[FileEntry]:
    """Содержимое папок и ещё `depth` уровней вглубь; обход обрывается на `limit` записях.

    Порядок — по приоритету: папки в том порядке, в каком переданы, и в каждой сначала её
    собственное содержимое, потом вложенное.
    """
    found: list[FileEntry] = []

    def walk(folder: Path, level: int) -> None:
        try:
            children = [child for child in sorted(folder.iterdir()) if _visible(child)]
        except OSError:
            return
        for child in children:
            if len(found) >= limit:
                return
            found.append(_entry(child))
        if level < depth:
            for child in children:
                if len(found) >= limit:
                    return
                if child.is_dir():
                    walk(child, level + 1)

    for folder in folders:
        walk(folder, 0)
    return found


def _known_folder(folder_id: str) -> Path | None:
    class Guid(ctypes.Structure):
        _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    guid = Guid.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    path = ctypes.c_wchar_p()
    get_path = ctypes.windll.shell32.SHGetKnownFolderPath
    get_path.argtypes = [ctypes.POINTER(Guid), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    try:
        if get_path(ctypes.byref(guid), 0, None, ctypes.byref(path)) != 0:
            return None
        return Path(path.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)


class WindowsFiles:
    """Настоящий backend: рабочий стол (свой и общий), «Документы», «Загрузки»."""

    def entries(self) -> list[FileEntry]:
        # Сканируем при каждом вызове: файлы на столе появляются и исчезают между командами.
        folders = [folder for folder_id in _FOLDER_IDS.values() if (folder := _known_folder(folder_id))]
        return scan(folders)

    def open(self, path: Path) -> None:
        os.startfile(path)


def register(registry: ToolRegistry, backend: FilesBackend, launched: LaunchLog) -> None:
    @registry.tool(
        description="Открыть файл или папку с рабочего стола, из «Документов» или «Загрузок» по названию.",
        params={"name": Param("string", "Название файла или папки без расширения, как назвал пользователь")},
    )
    def open_file(name: str) -> ToolResult:
        entries: dict[str, FileEntry] = {}
        for entry in backend.entries():
            # При одинаковых именах — первое: рабочий стол просматривается раньше «Загрузок».
            entries.setdefault(entry.name, entry)
        best = find_best(name, list(entries))
        if best is None:
            return ToolResult(ok=False, error=f"Файл или папка «{name}» не найдены")
        backend.open(entries[best].path)
        launched.mark()
        return ToolResult(ok=True, data={"opened": best}, speech=f"Открываю {best}.")
