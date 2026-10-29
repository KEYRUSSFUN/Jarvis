from collections.abc import Callable
from types import SimpleNamespace

import pytest

from jarvis.agent import Agent
from jarvis.llm import ToolCall
from jarvis.safety import ToolExecutor
from jarvis.tools import keys, winapi
from jarvis.tools.files import LaunchLog
from jarvis.tools.registry import ToolRegistry
from jarvis.tools.winapi import Window


@pytest.mark.parametrize(
    ("text", "vks"),
    [
        ("ctrl+s", [0x11, 0x53]),
        ("Ctrl + Shift + T", [0x11, 0x10, 0x54]),
        ("enter", [0x0D]),
        ("alt+f4", [0x12, 0x73]),
        ("control+return", [0x11, 0x0D]),
        ("win d", [0x5B, 0x44]),
        ("pagedown", [0x22]),
        ("del", [0x2E]),
        ("ctrl+5", [0x11, 0x35]),
    ],
)
def test_parse_keys(text, vks):
    assert keys.parse_keys(text) == vks


def test_parse_unknown_key_lists_allowed():
    with pytest.raises(ValueError, match="ctrl"):
        keys.parse_keys("ctrl+щ")


class FakeInput:
    def __init__(
        self,
        windows: list[Window],
        activates: bool = True,
        appear_after: int = 0,
        interrupt: winapi.InputInterrupted | None = None,
    ) -> None:
        self._windows = windows
        self._activates = activates
        self._appear_after = appear_after  # окна появляются на N-м опросе
        self._interrupt = interrupt  # ввод обрывается этим исключением
        self._polls = 0
        self.activated: list[int] = []
        self.typed: list[str] = []
        self.targets: list[int | None] = []
        self.pressed: list[list[int]] = []

    def windows(self) -> list[Window]:
        self._polls += 1
        return self._windows if self._polls >= self._appear_after else []

    def activate(self, hwnd: int) -> bool:
        self.activated.append(hwnd)
        return self._activates

    def type_text(self, text: str, target: int | None) -> None:
        if self._interrupt is not None:
            raise self._interrupt
        self.typed.append(text)
        self.targets.append(target)

    def press(self, vks: list[int]) -> None:
        if self._interrupt is not None:
            raise self._interrupt
        self.pressed.append(vks)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make(backend: FakeInput, launched: LaunchLog | None = None):
    registry = ToolRegistry()
    sleeps: list[float] = []
    clock = FakeClock()

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    keys.register(registry, backend, launched or LaunchLog(clock), sleep=sleep, clock=clock)
    return ToolExecutor(registry, confirm=lambda prompt: True), sleeps


WORD = [Window(5, "Отчёт.docx - Word", 1)]


def test_type_text_activates_window_then_types():
    backend = FakeInput(WORD)
    executor, _ = make(backend)
    result = executor.execute("type_text", {"text": "привет", "window": "Отчёт"})
    assert result.ok
    assert result.speech == "Напечатал."
    assert backend.activated == [5]
    assert backend.typed == ["привет"]
    assert backend.targets == [5]  # печать следит именно за этим окном


def test_type_text_same_titles_picks_topmost_window():
    # Окна идут сверху вниз (порядок EnumWindows): верхнее — первое.
    backend = FakeInput([Window(7, "Без имени — Блокнот", 1), Window(8, "Без имени — Блокнот", 2)])
    executor, _ = make(backend)
    result = executor.execute("type_text", {"text": "привет", "window": "Блокнот"})
    assert result.ok
    assert backend.activated == [7]


def test_type_text_window_with_extension_matches_title():
    backend = FakeInput(WORD)
    executor, _ = make(backend)
    result = executor.execute("type_text", {"text": "привет", "window": "Отчёт.docx"})
    assert result.ok
    assert backend.activated == [5]


def test_type_text_not_typed_if_window_did_not_activate():
    backend = FakeInput(WORD, activates=False)
    executor, _ = make(backend)
    result = executor.execute("type_text", {"text": "привет", "window": "Отчёт"})
    assert not result.ok
    assert backend.typed == []


def test_type_text_without_window_types_into_active():
    backend = FakeInput(WORD)
    executor, _ = make(backend)
    result = executor.execute("type_text", {"text": "привет"})
    assert result.ok
    assert backend.activated == []
    assert backend.typed == ["привет"]
    assert backend.targets == [None]  # окно, активное в момент начала печати


def test_type_text_waits_for_window_after_recent_launch():
    backend = FakeInput(WORD, appear_after=3)
    launched = LaunchLog()
    launched.mark()
    executor, sleeps = make(backend, launched)
    result = executor.execute("type_text", {"text": "привет", "window": "Отчёт"})
    assert result.ok
    assert backend.typed == ["привет"]
    assert sleeps == [0.1, 0.1]


def test_type_text_does_not_wait_without_recent_launch():
    backend = FakeInput([])
    executor, sleeps = make(backend)
    result = executor.execute("type_text", {"text": "привет", "window": "Отчёт"})
    assert not result.ok
    assert sleeps == []
    assert backend.typed == []


def test_type_text_gives_up_after_wait_limit():
    backend = FakeInput([])
    launched = LaunchLog()
    launched.mark()
    executor, sleeps = make(backend, launched)
    result = executor.execute("type_text", {"text": "привет", "window": "Отчёт"})
    assert not result.ok
    assert sum(sleeps) >= keys.WINDOW_WAIT_SECONDS


def test_type_text_keeps_newlines_and_yo():
    backend = FakeInput(WORD)
    executor, _ = make(backend)
    executor.execute("type_text", {"text": "Ёлка\n«ура»"})
    assert backend.typed == ["Ёлка\n«ура»"]


def test_press_keys_in_window():
    backend = FakeInput(WORD)
    executor, _ = make(backend)
    result = executor.execute("press_keys", {"keys": "ctrl+s", "window": "Отчёт"})
    assert result.ok
    assert result.speech == "Готово."
    assert backend.activated == [5]
    assert backend.pressed == [[0x11, 0x53]]


def test_press_keys_unknown_key_is_error():
    backend = FakeInput(WORD)
    executor, _ = make(backend)
    result = executor.execute("press_keys", {"keys": "ctrl+щ"})
    assert not result.ok
    assert backend.pressed == []


def test_blocked_injection_becomes_error_result():
    class Blocked(FakeInput):
        def press(self, vks: list[int]) -> None:
            raise OSError("SendInput вставил 0 из 4 событий")

    executor, _ = make(Blocked(WORD))
    result = executor.execute("press_keys", {"keys": "ctrl+s"})
    assert not result.ok
    assert "администратор" in result.error


@pytest.mark.parametrize(
    ("reason", "speech"),
    [
        (winapi.WINDOW_CHANGED, "Остановил печать: окно сменилось."),
        (winapi.KEY_PRESSED, "Остановил печать: нажата клавиша."),
    ],
)
def test_interrupted_typing_is_spoken_error(reason, speech):
    backend = FakeInput(WORD, interrupt=winapi.InputInterrupted(reason, 3, 6))
    executor, _ = make(backend)
    result = executor.execute("type_text", {"text": "привет", "window": "Отчёт"})
    assert not result.ok
    assert result.speech == speech  # с фразой агент не даёт модели второй ход
    assert "напечатано 3 из 6 символов" in result.error


def test_press_keys_refused_while_modifier_held_is_spoken_error():
    backend = FakeInput(WORD, interrupt=winapi.InputInterrupted(winapi.KEY_PRESSED))
    executor, _ = make(backend)
    result = executor.execute("press_keys", {"keys": "enter"})
    assert not result.ok
    assert result.speech == "Не стал нажимать: нажата клавиша."


def test_interrupted_typing_ends_command_without_second_llm_turn():
    class OneTurnLLM:
        """Отвечает одним вызовом type_text; второй запрос к модели — провал теста."""

        def __init__(self) -> None:
            self.turns = 0

        def stream(self, messages, tools):
            self.turns += 1
            assert self.turns == 1, "модель получила второй ход и могла бы напечатать текст заново"
            yield ToolCall("type_text", {"text": "длинное письмо"})

    registry = ToolRegistry()
    backend = FakeInput(WORD, interrupt=winapi.InputInterrupted(winapi.WINDOW_CHANGED, 2, 14))
    keys.register(registry, backend, LaunchLog())
    agent = Agent(OneTurnLLM(), registry, ToolExecutor(registry, confirm=lambda prompt: True), "Ты Jarvis.")
    spoken: list[str] = []
    agent.run("напиши письмо", spoken.append)
    assert spoken == ["Остановил печать: окно сменилось."]


def test_windows_input_passes_target_to_send_text(monkeypatch):
    calls = []
    monkeypatch.setattr(winapi, "send_text", lambda text, target=None: calls.append((text, target)))
    keys.WindowsInput().type_text("привет", 5)
    assert calls == [("привет", 5)]


# --- winapi: ввод через поддельный user32 ---


@pytest.mark.parametrize("vk", [0x25, 0x26, 0x27, 0x28, 0x21, 0x22, 0x23, 0x24, 0x2D, 0x2E, 0xA3, 0xA5, 0x5B, 0x5C])
def test_extended_keys(vk):
    assert winapi._is_extended(vk)


@pytest.mark.parametrize("vk", [0x0D, 0x11, 0x53, 0x1B, 0x70])
def test_plain_keys_are_not_extended(vk):
    assert not winapi._is_extended(vk)


@pytest.fixture(autouse=True)
def no_typing_delay(monkeypatch):
    monkeypatch.setattr(winapi, "_TYPE_DELAY", 0)


CTRL, ALT, LWIN, RWIN, ENTER = 0x11, 0x12, 0x5B, 0x5C, 0x0D
KEYUP, UNICODE = 0x2, 0x4


class FakeUser32:
    """user32 для тестов: активное окно и зажатые человеком клавиши задаёт тест, вставленный ввод запоминается."""

    def __init__(self, limits: list[int] | None = None) -> None:
        self.sent: list[tuple[int, int, int]] = []  # (vk, scan, flags) вставленных событий
        self.calls: list[int] = []  # сколько событий было в каждом вызове SendInput
        self.limits = list(limits or [])  # сколько событий вставит очередной вызов; кончились — все
        self.foreground = 5
        self.owners: dict[int, int] = {}  # всплывающее окно → его корневой владелец
        self.held: set[int] = set()
        self.after: dict[int, Callable[[], None]] = {}  # что случится после N-го вставленного события
        self.taps: list[int] = []  # keybd_event

    def SendInput(self, count, array, size):
        self.calls.append(count)
        injected = min(self.limits.pop(0), count) if self.limits else count
        for i in range(injected):
            item = array[i].ki
            self.sent.append((item.wVk, item.wScan, item.dwFlags))
            if len(self.sent) in self.after:
                self.after.pop(len(self.sent))()
        return injected

    def GetForegroundWindow(self):
        return self.foreground

    def GetAncestor(self, hwnd, flags):
        assert flags == winapi._GA_ROOTOWNER
        return self.owners.get(hwnd, hwnd)

    def GetAsyncKeyState(self, vk):
        return -32768 if vk in self.held else 0  # SHORT со старшим битом

    def IsIconic(self, hwnd):
        return False

    def keybd_event(self, vk, scan, flags, extra):
        self.taps.append(vk)

    def SetForegroundWindow(self, hwnd):
        self.foreground = hwnd
        return True


@pytest.fixture
def api(monkeypatch) -> FakeUser32:
    fake = FakeUser32()
    monkeypatch.setattr(winapi, "_api", fake)
    return fake


def chars(api: FakeUser32) -> str:
    """Что напечатано: символы из нажатий (Enter — перевод строки)."""
    typed = ""
    for vk, scan, flags in api.sent:
        if not flags & KEYUP:
            typed += "\n" if vk == ENTER else chr(scan)
    return typed


def test_send_keys_sets_extended_flag_on_arrows(api):
    winapi.send_keys([CTRL, 0x27])
    assert [(vk, flags) for vk, _, flags in api.sent] == [(CTRL, 0), (0x27, 1), (0x27, 3), (CTRL, 2)]
    assert api.calls == [4]  # одним пакетом


def test_send_keys_raises_when_injection_is_short(api):
    api.limits = [0]
    with pytest.raises(OSError, match="SendInput"):
        winapi.send_keys([CTRL, 0x53])
    assert api.sent == []


@pytest.mark.parametrize(
    ("injected", "expected"),
    [
        (1, [(CTRL, 0), (CTRL, 2)]),  # нажат только Ctrl
        (2, [(CTRL, 0), (0x53, 0), (0x53, 2), (CTRL, 2)]),  # нажаты оба, не отпущен ни один
        (3, [(CTRL, 0), (0x53, 0), (0x53, 2), (CTRL, 2)]),  # S уже отпущена, Ctrl — нет
    ],
)
def test_send_keys_releases_what_was_pressed_on_partial_injection(api, injected, expected):
    api.limits = [injected]
    with pytest.raises(OSError, match="SendInput"):
        winapi.send_keys([CTRL, 0x53])
    assert [(vk, flags) for vk, _, flags in api.sent] == expected


@pytest.mark.parametrize("vk", [CTRL, ALT, LWIN, RWIN])
def test_send_keys_refuses_while_modifier_held(api, vk):
    # Зажатый человеком модификатор сложился бы с нашими клавишами: Enter стал бы Ctrl+Enter.
    api.held = {vk}
    with pytest.raises(winapi.InputInterrupted) as error:
        winapi.send_keys([ENTER])
    assert error.value.reason == winapi.KEY_PRESSED
    assert api.sent == []


def test_send_text_raises_when_injection_is_short(api):
    api.limits = [0]
    with pytest.raises(OSError, match="SendInput"):
        winapi.send_text("привет")


def test_send_text_sends_each_event_separately(api):
    # Блокнот Windows 11 путает символы при вставке пакетом, поэтому по одному событию за вызов.
    winapi.send_text("а б\r\n")
    assert api.calls == [1] * 8
    assert chars(api) == "а б\n"


def test_send_text_types_everything_into_unchanged_window(api):
    winapi.send_text("Ёлка\nура")
    assert chars(api) == "Ёлка\nура"


@pytest.mark.parametrize("k", [1, 3, 5])
def test_send_text_stops_when_foreground_window_changes(api, k):
    api.after[2 * k] = lambda: setattr(api, "foreground", 9)  # каждый символ — нажатие и отпускание
    with pytest.raises(winapi.InputInterrupted) as error:
        winapi.send_text("привет")
    assert error.value.reason == winapi.WINDOW_CHANGED
    assert (error.value.typed, error.value.total) == (k, 6)
    assert f"напечатано {k} из 6 символов" in str(error.value)
    assert chars(api) == "привет"[:k]


def test_send_text_continues_into_owned_popup(api):
    # Подсказка автодополнения или диалог принадлежат тому же окну — печать не прерывается.
    api.owners = {7: 5}
    api.after[2] = lambda: setattr(api, "foreground", 7)
    winapi.send_text("привет")
    assert chars(api) == "привет"


def test_send_text_stops_when_window_disappears(api):
    api.after[2] = lambda: setattr(api, "foreground", 0)  # активного окна нет: ввод ушёл бы неизвестно куда
    with pytest.raises(winapi.InputInterrupted) as error:
        winapi.send_text("привет")
    assert error.value.reason == winapi.WINDOW_CHANGED
    assert chars(api) == "п"


def test_send_text_into_target_that_is_no_longer_active(api):
    # Окно активировали, но до начала печати его перекрыли — не печатаем ничего.
    api.foreground = 9
    with pytest.raises(winapi.InputInterrupted) as error:
        winapi.send_text("привет", target=5)
    assert error.value.reason == winapi.WINDOW_CHANGED
    assert api.sent == []


def test_send_text_without_active_window_types_nothing(api):
    api.foreground = 0
    with pytest.raises(winapi.InputInterrupted) as error:
        winapi.send_text("привет")
    assert error.value.reason == winapi.NO_WINDOW
    assert api.sent == []


@pytest.mark.parametrize("vk", [CTRL, ALT, LWIN, RWIN])
def test_send_text_does_not_start_while_modifier_held(api, vk):
    api.held = {vk}
    with pytest.raises(winapi.InputInterrupted) as error:
        winapi.send_text("привет")
    assert error.value.reason == winapi.KEY_PRESSED
    assert (error.value.typed, api.sent) == (0, [])


def test_send_text_stops_when_push_to_talk_ctrl_pressed_midway(api):
    # Левый Ctrl — клавиша записи: человек перебивает Jarvis. Enter после этого стал бы Ctrl+Enter («отправить»).
    api.after[4] = lambda: api.held.add(CTRL)
    with pytest.raises(winapi.InputInterrupted) as error:
        winapi.send_text("ok\nотправлю позже")
    assert error.value.reason == winapi.KEY_PRESSED
    assert error.value.typed == 2
    assert chars(api) == "ok"
    assert all(vk != ENTER for vk, _, _ in api.sent)


@pytest.mark.parametrize("text", ["\n", "я"])
def test_send_text_releases_key_even_if_interrupted_between_press_and_release(api, monkeypatch, text):
    def interrupted_sleep(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(winapi, "time", SimpleNamespace(sleep=interrupted_sleep))
    with pytest.raises(KeyboardInterrupt):
        winapi.send_text(text)
    assert len(api.sent) == 2
    (_, _, down), (_, _, up) = api.sent
    assert not down & KEYUP and up & KEYUP


def test_activate_taps_f24_not_alt(api):
    # Одиночный Alt переводит окно в режим меню, и напечатанный следом текст уходит в меню (проверено в Блокноте).
    api.foreground = 1
    assert winapi.activate(7)
    assert ALT not in api.taps
    assert api.taps == [0x87, 0x87]  # F24: нажать и отпустить
