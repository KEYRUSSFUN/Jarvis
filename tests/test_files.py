import ctypes
from pathlib import Path

from jarvis.safety import ToolExecutor
from jarvis.tools import files
from jarvis.tools.files import FileEntry, LaunchLog, scan
from jarvis.tools.registry import ToolRegistry


def executor_for(register, *args) -> tuple[ToolExecutor, ToolRegistry]:
    registry = ToolRegistry()
    register(registry, *args)
    return ToolExecutor(registry, confirm=lambda prompt: True), registry


class FakeFiles:
    def __init__(self, entries: list[FileEntry]) -> None:
        self._entries = entries
        self.opened: list[Path] = []

    def entries(self) -> list[FileEntry]:
        return self._entries

    def open(self, path: Path) -> None:
        self.opened.append(path)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_scan_finds_files_folders_and_shortcuts_one_level_deep(tmp_path):
    (tmp_path / "Отчёт.docx").write_text("")
    (tmp_path / "Warframe.url").write_text("")
    (tmp_path / "Исходники" / "глубже").mkdir(parents=True)
    (tmp_path / "Исходники" / "main.py").write_text("")
    (tmp_path / "Исходники" / "глубже" / "скрыто.txt").write_text("")
    names = {e.name for e in scan([tmp_path])}
    assert {"Отчёт", "Warframe", "Исходники", "main", "глубже"} <= names
    assert "скрыто" not in names


def test_scan_skips_hidden_files(tmp_path):
    (tmp_path / "видимый.txt").write_text("")
    hidden = tmp_path / "прятки.txt"
    hidden.write_text("")
    ctypes.windll.kernel32.SetFileAttributesW(str(hidden), 2)
    (tmp_path / "desktop.ini").write_text("")
    (tmp_path / ".git").mkdir()
    names = {e.name for e in scan([tmp_path])}
    assert "видимый" in names
    assert not names & {"прятки", "desktop", ".git"}


def test_open_file_opens_best_match_and_marks_launch():
    backend = FakeFiles([FileEntry("Отчёт", Path("Отчёт.docx")), FileEntry("Фото", Path("Фото.png"))])
    launched = LaunchLog()
    executor, _ = executor_for(files.register, backend, launched)
    result = executor.execute("open_file", {"name": "отчет"})
    assert backend.opened == [Path("Отчёт.docx")]
    assert result.speech == "Открываю Отчёт."
    assert launched.recent(20)


def test_open_file_same_name_opens_first_entry():
    # Рабочий стол просматривается раньше «Загрузок»: одноимённый файл берём с рабочего стола.
    backend = FakeFiles([FileEntry("Отчёт", Path("Desktop/Отчёт.docx")), FileEntry("Отчёт", Path("Downloads/Отчёт.docx"))])
    executor, _ = executor_for(files.register, backend, LaunchLog())
    executor.execute("open_file", {"name": "отчет"})
    assert backend.opened == [Path("Desktop/Отчёт.docx")]


def test_scan_lists_folder_order_and_own_files_before_nested(tmp_path):
    desktop, downloads = tmp_path / "desktop", tmp_path / "downloads"
    (desktop / "Архив").mkdir(parents=True)
    (desktop / "Архив" / "Отчёт.docx").write_text("")  # «Архив» по алфавиту раньше «Отчёт»
    (desktop / "Отчёт.docx").write_text("")
    downloads.mkdir()
    (downloads / "Отчёт.docx").write_text("")
    reports = [e.path for e in scan([desktop, downloads]) if e.name == "Отчёт"]
    assert reports == [desktop / "Отчёт.docx", desktop / "Архив" / "Отчёт.docx", downloads / "Отчёт.docx"]


def test_open_file_unknown_name_is_error_without_speech():
    backend = FakeFiles([FileEntry("Отчёт", Path("Отчёт.docx"))])
    launched = LaunchLog()
    executor, _ = executor_for(files.register, backend, launched)
    result = executor.execute("open_file", {"name": "квитанция"})
    assert not result.ok
    assert result.speech is None
    assert backend.opened == []
    assert not launched.recent(20)


def test_launch_log_recent():
    clock = FakeClock()
    log = LaunchLog(clock)
    assert not log.recent(20)
    log.mark()
    clock.now += 19
    assert log.recent(20)
    clock.now += 2
    assert not log.recent(20)
