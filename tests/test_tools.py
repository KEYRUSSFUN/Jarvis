from datetime import datetime

import pytest

from jarvis.safety import ToolExecutor
from jarvis.tools import apps, media, system, web
from jarvis.tools.apps import AppEntry, Window
from jarvis.tools.registry import Risk, ToolRegistry


def executor_for(register, *args, **kwargs) -> tuple[ToolExecutor, ToolRegistry]:
    registry = ToolRegistry()
    register(registry, *args, **kwargs)
    return ToolExecutor(registry, confirm=lambda prompt: True), registry


class FakeApps:
    def __init__(self) -> None:
        self.entries = [
            AppEntry("Telegram", "TelegramDesktop.exe_id"),
            AppEntry("Google Chrome", "Chrome"),
            AppEntry("Блокнот", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"),
        ]
        self.open_windows = [Window(101, "Без имени — Блокнот"), Window(202, "Telegram")]
        self.launched: list[str] = []
        self.closed: list[int] = []

    def start_apps(self) -> list[AppEntry]:
        return self.entries

    def launch(self, app_id: str) -> None:
        self.launched.append(app_id)

    def windows(self) -> list[Window]:
        return self.open_windows

    def close(self, hwnd: int) -> None:
        self.closed.append(hwnd)


class TestApps:
    def test_open_app_launches_matching_entry(self):
        backend = FakeApps()
        executor, _ = executor_for(apps.register, backend, aliases={})
        result = executor.execute("open_app", {"app": "хром"})
        assert backend.launched == ["Chrome"]
        assert result.speech == "Открываю Google Chrome."

    def test_open_app_uses_alias(self):
        backend = FakeApps()
        executor, _ = executor_for(apps.register, backend, aliases={"телега": "Telegram"})
        executor.execute("open_app", {"app": "телега"})
        assert backend.launched == ["TelegramDesktop.exe_id"]

    def test_open_unknown_app_launches_nothing_and_lets_llm_answer(self):
        backend = FakeApps()
        executor, _ = executor_for(apps.register, backend, aliases={})
        result = executor.execute("open_app", {"app": "фотошоп"})
        assert not result.ok
        assert result.speech is None
        assert backend.launched == []

    def test_close_app_closes_matching_window(self):
        backend = FakeApps()
        executor, _ = executor_for(apps.register, backend, aliases={})
        result = executor.execute("close_app", {"app": "блокнот"})
        assert backend.closed == [101]
        assert result.ok

    def test_close_app_without_window_closes_nothing(self):
        backend = FakeApps()
        executor, _ = executor_for(apps.register, backend, aliases={})
        result = executor.execute("close_app", {"app": "калькулятор"})
        assert not result.ok
        assert backend.closed == []


class FakeVolume:
    def __init__(self, level: float, muted: bool = False) -> None:
        self.level = level
        self.muted = muted

    def get(self) -> float:
        return self.level

    def set(self, level: float) -> None:
        self.level = level

    def set_mute(self, muted: bool) -> None:
        self.muted = muted


class TestMedia:
    @pytest.mark.parametrize(("percent", "level", "speech"), [(40, 0.4, "Громкость 40%."), (150, 1.0, "Громкость 100%."), (-5, 0.0, "Громкость 0%.")])
    def test_set_volume_clamps_to_0_100(self, percent, level, speech):
        volume = FakeVolume(0.5)
        executor, _ = executor_for(media.register, volume, press_key=lambda vk: None)
        result = executor.execute("set_volume", {"percent": percent})
        assert volume.level == pytest.approx(level)
        assert result.speech == speech

    def test_set_volume_unmutes(self):
        volume = FakeVolume(0.5, muted=True)
        executor, _ = executor_for(media.register, volume, press_key=lambda vk: None)
        executor.execute("set_volume", {"percent": 30})
        assert volume.muted is False

    @pytest.mark.parametrize(("start", "delta", "level"), [(0.57, 10, 0.67), (0.95, 10, 1.0), (0.05, -10, 0.0)])
    def test_change_volume_is_relative_and_clamped(self, start, delta, level):
        volume = FakeVolume(start)
        executor, _ = executor_for(media.register, volume, press_key=lambda vk: None)
        executor.execute("change_volume", {"delta": delta})
        assert volume.level == pytest.approx(level)

    def test_mute_and_unmute(self):
        volume = FakeVolume(0.5)
        executor, _ = executor_for(media.register, volume, press_key=lambda vk: None)
        assert executor.execute("mute", {}).speech == "Звук выключен."
        assert volume.muted is True
        assert executor.execute("unmute", {}).speech == "Звук включён."
        assert volume.muted is False

    @pytest.mark.parametrize(("action", "vk"), [("play_pause", 0xB3), ("next", 0xB0), ("previous", 0xB1), ("stop", 0xB2)])
    def test_media_control_presses_media_key(self, action, vk):
        pressed: list[int] = []
        executor, _ = executor_for(media.register, FakeVolume(0.5), press_key=pressed.append)
        assert executor.execute("media_control", {"action": action}).ok
        assert pressed == [vk]


class TestWeb:
    def test_open_url_adds_https(self):
        opened: list[str] = []
        executor, _ = executor_for(web.register, "https://yandex.ru/search/?text={query}", open_in_browser=opened.append)
        result = executor.execute("open_url", {"url": "youtube.com"})
        assert opened == ["https://youtube.com"]
        assert result.speech == "Открываю youtube.com."

    def test_open_url_keeps_existing_scheme(self):
        opened: list[str] = []
        executor, _ = executor_for(web.register, "https://yandex.ru/search/?text={query}", open_in_browser=opened.append)
        executor.execute("open_url", {"url": "http://localhost:8000/docs"})
        assert opened == ["http://localhost:8000/docs"]

    @pytest.mark.parametrize(
        ("query", "url"),
        [
            ("python asyncio", "https://yandex.ru/search/?text=python+asyncio"),
            ("кот", "https://yandex.ru/search/?text=%D0%BA%D0%BE%D1%82"),
        ],
    )
    def test_web_search_encodes_query(self, query, url):
        opened: list[str] = []
        executor, _ = executor_for(web.register, "https://yandex.ru/search/?text={query}", open_in_browser=opened.append)
        executor.execute("web_search", {"query": query})
        assert opened == [url]


class TestSystem:
    def test_datetime_in_russian(self):
        executor, _ = executor_for(system.register, run=lambda cmd: None, now=lambda: datetime(2026, 10, 27, 9, 5))
        result = executor.execute("get_datetime", {})
        assert result.data == {"time": "09:05", "date": "27 октября 2026", "weekday": "вторник"}

    @pytest.mark.parametrize(
        ("action", "command"),
        [
            ("выключить", ["shutdown", "/s", "/t", "10"]),
            ("перезагрузить", ["shutdown", "/r", "/t", "10"]),
        ],
    )
    def test_power_runs_delayed_shutdown(self, action, command):
        commands: list[list[str]] = []
        executor, _ = executor_for(system.register, run=commands.append, now=datetime.now)
        assert executor.execute("power", {"action": action}).ok
        assert commands == [command]

    def test_power_requires_confirmation(self):
        _, registry = executor_for(system.register, run=lambda cmd: None, now=datetime.now)
        assert registry.get("power").risk is Risk.CONFIRM
