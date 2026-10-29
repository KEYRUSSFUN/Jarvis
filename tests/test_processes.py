import os
from dataclasses import replace
from pathlib import PureWindowsPath
from types import SimpleNamespace

import psutil
import pytest

from jarvis.tools import processes, winapi
from jarvis.tools.processes import CLOSE_WAIT_SECONDS, ProcessInfo, close_program, find_targets

STEAM = ProcessInfo(10, 1, r"D:\Steam\steam.exe", ("steam", "Steam"), False)
HELPER = ProcessInfo(11, 10, r"D:\Steam\bin\steamwebhelper.exe", ("steamwebhelper", "Steam Client WebHelper"), False)
WALLPAPER = ProcessInfo(
    20, 1, r"C:\SteamLibrary\steamapps\common\wallpaper_engine\wallpaper64.exe", ("wallpaper64", "wallpaper engine"), False
)
EXPLORER = ProcessInfo(30, 1, r"C:\Windows\explorer.exe", ("explorer", "Проводник"), True)


class FakeProcesses:
    """Процессы, закрывающиеся по просьбе только если им так задано (stubborn — упрямые).

    asks — в ответ на просьбу закрыться показывают окно («Выйти во время записи?») и остаются жить.
    """

    def __init__(
        self,
        processes: list[ProcessInfo],
        stubborn: set[int] = frozenset(),
        deny_kill: bool = False,
        asks: set[int] = frozenset(),
    ) -> None:
        self._processes = processes
        self.alive = {p.pid for p in processes}
        self.windowed = {p.pid for p in processes if p.has_window}
        self.stubborn = set(stubborn)
        self.asks = set(asks)
        self.deny_kill = deny_kill
        self.close_requests: list[int] = []
        self.hidden_close_requests: list[int] = []  # просьбы, ушедшие и в скрытые окна
        self.killed: list[int] = []

    def processes(self) -> list[ProcessInfo]:
        return [p for p in self._processes if p.pid in self.alive]

    def request_close(self, pid: int, include_hidden: bool) -> None:
        self.close_requests.append(pid)
        if include_hidden:
            self.hidden_close_requests.append(pid)
        if pid in self.asks:
            self.windowed.add(pid)
        elif pid not in self.stubborn:
            self.alive.discard(pid)

    def has_window(self, pid: int) -> bool:
        return pid in self.windowed and pid in self.alive

    def is_alive(self, pid: int) -> bool:
        return pid in self.alive

    def kill(self, pid: int) -> None:
        if self.deny_kill:
            raise PermissionError(pid)
        self.killed.append(pid)
        self.alive.discard(pid)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def clock(self) -> float:
        return self.now


def run(app, backend, protected=frozenset()):
    time = FakeTime()
    return close_program(app, backend, set(protected), sleep=time.sleep, clock=time.clock), time


def test_find_targets_by_description_and_folder():
    processes = [STEAM, HELPER, WALLPAPER, EXPLORER]
    assert find_targets("Steam", processes, set()) == [STEAM]
    assert find_targets("Wallpaper Engine", processes, set()) == [WALLPAPER]


def test_find_targets_groups_same_exe_root_first():
    child = replace(STEAM, pid=12, ppid=10)
    # Дочерний указан раньше корневого — корневой всё равно первый.
    assert find_targets("Steam", [child, STEAM, HELPER], set()) == [STEAM, child]


def test_protected_processes_are_never_targets():
    assert find_targets("проводник", [EXPLORER, STEAM], set()) == []
    assert find_targets("Steam", [STEAM], {10}) == []


def test_close_program_kills_only_after_graceful_close_fails():
    backend = FakeProcesses([STEAM], stubborn={10})
    result, time = run("Steam", backend)
    assert backend.close_requests == [10]
    assert backend.hidden_close_requests == [10]  # у программы в трее окно скрытое
    assert backend.killed == [10]
    assert time.now >= CLOSE_WAIT_SECONDS
    assert result.ok
    assert result.speech == "Закрываю steam."


def test_close_program_does_not_kill_when_process_exits():
    backend = FakeProcesses([STEAM])
    result, _ = run("Steam", backend)
    assert backend.close_requests == [10]
    assert backend.killed == []
    assert result.ok


def test_close_program_never_kills_process_with_window():
    windowed = replace(STEAM, has_window=True)
    backend = FakeProcesses([windowed], stubborn={10})
    result, time = run("Steam", backend)
    assert backend.close_requests == [10]
    assert backend.killed == []
    assert result.ok
    # Только видимые окна, как крестиком: скрытое окно трея не трогаем, и ждать нечего.
    assert backend.hidden_close_requests == []
    assert time.now == 0


def test_close_program_never_kills_windowless_sibling_of_windowed_process():
    root = replace(STEAM, has_window=True)
    child = replace(STEAM, pid=12, ppid=10)
    backend = FakeProcesses([root, child], stubborn={10, 12})
    result, time = run("Steam", backend)
    assert backend.close_requests == [10, 12]
    assert backend.hidden_close_requests == []
    assert backend.killed == []
    assert time.now == 0
    assert result.ok


@pytest.mark.parametrize("asking", [10, 12])
def test_close_program_does_not_kill_when_window_appears_after_close_request(asking):
    # Программа ответила на просьбу окном «Выйти во время записи?» — решает человек, а не kill через 3 с.
    child = replace(STEAM, pid=12, ppid=10)
    backend = FakeProcesses([STEAM, child], stubborn={10, 12}, asks={asking})
    result, time = run("Steam", backend)
    assert backend.close_requests == [10, 12]
    assert time.now >= CLOSE_WAIT_SECONDS
    assert backend.killed == []
    assert result.ok
    assert result.speech == "Закрываю steam."


def test_close_program_elevated_process_reports_permission_error():
    # Процесс от администратора находится как обычный (exe читается), а kill отвечает «нет прав».
    elevated = ProcessInfo(40, 1, r"C:\Program Files\Steam\steamservice.exe", ("steamservice", "Steam"), False)
    backend = FakeProcesses([elevated], stubborn={40}, deny_kill=True)
    result, _ = run("Steam", backend)
    assert not result.ok
    assert "прав" in result.error


def test_close_program_reports_permission_error():
    backend = FakeProcesses([STEAM], stubborn={10}, deny_kill=True)
    result, _ = run("Steam", backend)
    assert not result.ok
    assert "прав" in result.error


def test_close_program_unknown_returns_none():
    backend = FakeProcesses([STEAM])
    result, _ = run("Фотошоп", backend)
    assert result is None
    assert backend.close_requests == []


# --- нечёткий поиск среди ~1000 названий процессов ---


def program(pid: int, exe: str, *names: str) -> ProcessInfo:
    return ProcessInfo(pid, 1, exe, (PureWindowsPath(exe).stem, *names), False)


# Названия с этой машины: одно совпавшее слово цепляло чужую программу.
STRANGERS = [
    program(50, r"C:\Program Files\FlyFrogLLC\Happ\Happ.exe", "Happ Proxy Client"),
    program(51, r"C:\Program Files\Microsoft Office\root\AI\ai.exe", "Microsoft 365 Local AI Host"),
    program(52, r"C:\Program Files\PostgreSQL\17\bin\postgres.exe", "PostgreSQL Server"),
    program(53, r"C:\Program Files\Razer\RzAppEngine\rzappengine.exe", "Razer AppEngine"),
]
KNOWN = [
    STEAM,
    HELPER,
    WALLPAPER,
    program(60, r"C:\Users\user\AppData\Local\Discord\app-1.0.9205\Discord.exe", "Discord", "app 1.0.9205"),
    program(61, r"C:\Users\user\AppData\Roaming\Telegram Desktop\Telegram.exe", "Telegram Desktop"),
    program(62, r"C:\Users\user\AppData\Roaming\Spotify\Spotify.exe", "Spotify"),
]


@pytest.mark.parametrize("query", ["клиент", "хост", "сервер", "браузер"])
def test_find_targets_rejects_single_word_match_to_long_name(query):
    assert find_targets(query, STRANGERS + KNOWN, set()) == []


@pytest.mark.parametrize(
    ("query", "pid"),
    [("Steam", 10), ("Wallpaper Engine", 20), ("дискорд", 60), ("телеграм", 61), ("спотифай", 62)],
)
def test_find_targets_still_finds_programs_by_name(query, pid):
    assert [p.pid for p in find_targets(query, STRANGERS + KNOWN, set())] == [pid]


# --- только процессы своего сеанса: службы (сеанс 0) не цели ---


@pytest.mark.parametrize(
    ("username", "session", "expected"),
    [
        ("PC\\user", 1, True),
        (None, 1, True),  # от администратора в нашем сеансе: найдём и честно скажем «не хватает прав»
        (None, 0, False),  # служба: PostgreSQL, Steam Client Service
        ("PC\\user", 0, False),
        (None, None, False),  # сеанс не прочитать
        ("PC\\other", 1, False),  # чужой пользователь
    ],
)
def test_is_users_process(username, session, expected):
    assert processes._is_users(username, session, "PC\\user", 1) is expected


def test_windows_backend_lists_only_own_session_processes(monkeypatch):
    rows = [
        {"pid": 10, "ppid": 1, "exe": r"D:\Steam\steam.exe", "username": "PC\\user"},
        {"pid": 40, "ppid": 1, "exe": r"C:\Program Files (x86)\Steam\bin\steamservice.exe", "username": None},
        {"pid": 41, "ppid": 1, "exe": r"C:\Program Files\PostgreSQL\17\bin\postgres.exe", "username": None},
        {"pid": 42, "ppid": 1, "exe": r"D:\Tools\elevated.exe", "username": None},
        {"pid": 43, "ppid": 1, "exe": None, "username": "PC\\user"},
    ]
    sessions = {os.getpid(): 1, 10: 1, 40: 0, 42: 1, 43: 1}  # у 41 сеанс не прочитать
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [SimpleNamespace(info=row) for row in rows])
    monkeypatch.setattr(psutil, "Process", lambda: SimpleNamespace(username=lambda: "PC\\user"))
    monkeypatch.setattr(processes, "session_id", sessions.get)
    monkeypatch.setattr(processes, "_read_version_strings", lambda exe: [])
    monkeypatch.setattr(winapi, "visible_windows", lambda: [])
    assert [p.pid for p in processes.WindowsProcesses().processes()] == [10, 42]


def test_session_id_reads_own_session():
    own = processes.session_id(os.getpid())
    assert isinstance(own, int)
    assert processes.session_id(4) != own  # System — не наш сеанс (или не прочитать)


def test_windows_dir_comes_from_system_root():
    assert processes._WINDOWS_DIR == PureWindowsPath(os.environ.get("SystemRoot") or r"C:\Windows")
