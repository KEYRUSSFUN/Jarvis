"""Закрытие программ, живущих в трее без окна (Steam, Wallpaper Engine)."""

import ctypes
import functools
import os
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Protocol

from jarvis.tools import winapi
from jarvis.tools.matching import best_match, find_best
from jarvis.tools.registry import ToolResult

# Системные и «свои» процессы: их не закрываем, что бы ни попросили.
PROTECTED_NAMES = {"explorer", "ollama", "ollama app", "llama-server", "jarvis"}
_WINDOWS_DIR = PureWindowsPath(os.environ.get("SystemRoot") or r"C:\Windows")

# Процессов около тысячи названий, и одно совпавшее слово цепляет чужое: «клиент» → «Happ Proxy Client»,
# «сервер» → «PostgreSQL Server». Поэтому название должно быть похоже и целиком, а не только словом.
MIN_WHOLE_SCORE = 60

# Сколько ждём, пока программа закроется сама, прежде чем завершить её принудительно.
CLOSE_WAIT_SECONDS = 3
_POLL_SECONDS = 0.2


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    exe: str
    names: tuple[str, ...]  # имя exe, описание, продукт, папка — всё, по чему программу могут назвать
    has_window: bool


class ProcessesBackend(Protocol):
    def processes(self) -> list[ProcessInfo]: ...
    # WM_CLOSE в окна процесса: только видимые или, если include_hidden, и скрытые (окно программы в трее).
    def request_close(self, pid: int, include_hidden: bool) -> None: ...
    def has_window(self, pid: int) -> bool: ...  # есть ли у процесса видимое окно прямо сейчас
    def is_alive(self, pid: int) -> bool: ...
    def kill(self, pid: int) -> None: ...  # PermissionError, если не хватает прав


def _is_protected(process: ProcessInfo, protected_pids: set[int]) -> bool:
    if process.pid in protected_pids:
        return True
    exe = PureWindowsPath(process.exe)
    if _WINDOWS_DIR in (exe, *exe.parents) or exe.stem.lower() in PROTECTED_NAMES:
        return True
    return any(name.lower() in PROTECTED_NAMES for name in process.names)


def find_targets(app: str, processes: list[ProcessInfo], protected_pids: set[int]) -> list[ProcessInfo]:
    """Процессы программы по названию: все с тем же exe, корневые (без родителя из этой же программы) первыми."""
    candidates = [p for p in processes if not _is_protected(p, protected_pids)]
    by_name = {name: p for p in reversed(candidates) for name in p.names}  # при равных именах — более ранний процесс
    match = best_match(app, list(by_name))
    if match is None or match.whole < MIN_WHOLE_SCORE:
        return []
    exe = by_name[match.name].exe.lower()
    group = [p for p in candidates if p.exe.lower() == exe]
    group_pids = {p.pid for p in group}
    return sorted(group, key=lambda p: p.ppid in group_pids)  # сортировка устойчива: порядок внутри групп прежний


def close_program(
    app: str,
    backend: ProcessesBackend,
    protected_pids: set[int],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ToolResult | None:
    """Закрывает программу из трея. None — такой программы среди процессов нет."""
    targets = find_targets(app, backend.processes(), protected_pids)
    if not targets:
        return None
    root = targets[0]
    name = find_best(app, list(root.names)) or root.names[0]
    closing = ToolResult(ok=True, data={"closed": name}, speech=f"Закрываю {name}.")

    # У программы есть окно — решает она сама (может спросить «сохранить?»): закрываем только видимые окна,
    # как крестиком, и ни один её процесс, в том числе безоконные дочерние, принудительно не трогаем.
    # Скрытые окна не трогаем: программа, сворачивающаяся в трей, осталась бы работать без значка.
    if any(p.has_window for p in targets):
        for process in targets:
            backend.request_close(process.pid, include_hidden=False)
        return closing

    for process in targets:
        backend.request_close(process.pid, include_hidden=True)
    deadline = clock() + CLOSE_WAIT_SECONDS
    while any(backend.is_alive(p.pid) for p in targets) and clock() < deadline:
        sleep(_POLL_SECONDS)

    alive = [p for p in targets if backend.is_alive(p.pid)]
    # В ответ на просьбу закрыться программа могла показать окно («Выйти во время записи?»):
    # тогда решает человек, принудительно не завершаем ничего.
    if any(backend.has_window(p.pid) for p in alive):
        return closing
    for process in alive:
        if not backend.is_alive(process.pid):
            continue  # дочерний мог завершиться вместе с корневым
        try:
            backend.kill(process.pid)
        except PermissionError:
            return ToolResult(ok=False, error=f"Не хватает прав, чтобы закрыть «{app}»")
    return closing


_VERSION_STRINGS = ("FileDescription", "ProductName")


def _load_version_dll():
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    version.VerQueryValueW.restype = wintypes.BOOL
    return version


def _read_version_strings(exe: str) -> list[str]:
    """FileDescription и ProductName из ресурсов exe; пусто, если их нет или файл недоступен."""
    api = _load_version_dll()
    size = api.GetFileVersionInfoSizeW(exe, None)
    if not size:
        return []
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(exe, 0, size, data):
        return []

    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not api.VerQueryValueW(data, r"\VarFileInfo\Translation", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 4:
        return []
    language, codepage = ctypes.cast(pointer, ctypes.POINTER(wintypes.WORD * 2)).contents  # первая пара
    found = []
    for key in _VERSION_STRINGS:
        path = rf"\StringFileInfo\{language:04x}{codepage:04x}\{key}"
        if api.VerQueryValueW(data, path, ctypes.byref(pointer), ctypes.byref(length)) and length.value:
            value = ctypes.wstring_at(pointer.value, length.value).rstrip("\0").strip()
            if value:
                found.append(value)
    return found


@functools.cache
def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    return kernel32


def session_id(pid: int) -> int | None:
    """Сеанс входа, в котором работает процесс; None — Windows не дала его прочитать (так у служб)."""
    session = wintypes.DWORD()
    if not _kernel32().ProcessIdToSessionId(pid, ctypes.byref(session)):
        return None
    return session.value


def _is_users(username: str | None, session: int | None, user: str, own_session: int | None) -> bool:
    """Процесс того же пользователя, что и Jarvis, и в том же сеансе входа.

    Службы (PostgreSQL, Steam Client Service) работают в сеансе 0 или их сеанс не прочитать — не наши.
    username=None у процессов с повышенными правами (psutil не смог открыть токен): если сеанс наш,
    оставляем их, чтобы kill честно ответил «не хватает прав».
    """
    if session is None or session != own_session:
        return False
    return username in (None, user)


def protected_pids() -> set[int]:
    """Сам Jarvis и все его родители (терминал, uv): их закрывать нельзя."""
    import psutil

    me = psutil.Process()
    return {me.pid, *(parent.pid for parent in me.parents())}


class WindowsProcesses:
    """Настоящий backend: процессы текущего пользователя через psutil, окна через WinAPI."""

    def __init__(self) -> None:
        self._version_names: dict[str, list[str]] = {}

    def _names(self, exe: str) -> tuple[str, ...]:
        if exe not in self._version_names:
            try:
                self._version_names[exe] = _read_version_strings(exe)
            except OSError:
                self._version_names[exe] = []
        path = PureWindowsPath(exe)
        folder = path.parent.name.replace("_", " ").replace("-", " ")
        names = [path.stem, *self._version_names[exe], folder]
        return tuple(dict.fromkeys(name for name in names if name))  # без пустых и повторов

    def processes(self) -> list[ProcessInfo]:
        import psutil

        with_window = {window.pid for window in winapi.visible_windows()}
        user = psutil.Process().username()
        own_session = session_id(os.getpid())
        found = []
        for process in psutil.process_iter(["pid", "ppid", "exe", "username"]):
            info = process.info
            # exe=None — системный процесс, не показать.
            if not info["exe"] or not _is_users(info["username"], session_id(info["pid"]), user, own_session):
                continue
            found.append(ProcessInfo(info["pid"], info["ppid"], info["exe"], self._names(info["exe"]), info["pid"] in with_window))
        return found

    def request_close(self, pid: int, include_hidden: bool) -> None:
        # Скрытые окна — для программы в трее: WM_CLOSE в её невидимое окно закрывает её целиком.
        if include_hidden:
            handles = winapi.process_windows(pid)
        else:
            handles = [window.hwnd for window in winapi.visible_windows() if window.pid == pid]
        for hwnd in handles:
            winapi.post_close(hwnd)

    def has_window(self, pid: int) -> bool:
        return any(window.pid == pid for window in winapi.visible_windows())

    def is_alive(self, pid: int) -> bool:
        import psutil

        return psutil.pid_exists(pid)

    def kill(self, pid: int) -> None:
        import psutil

        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as error:
            raise PermissionError(pid) from error
