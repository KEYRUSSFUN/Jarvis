"""Общие вызовы WinAPI: окна, фокус и ввод с клавиатуры."""

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

_WM_CLOSE = 0x0010
_IGNORED_TITLES = {"Program Manager"}

_SW_RESTORE = 9
_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
# Без флага «расширенная» стрелки и Home/End приходят как клавиши цифрового блока.
# PageUp..Down, End, Home, стрелки, Insert, Delete, обе клавиши Win, правые Ctrl и Alt.
_EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0xA3, 0xA5}
_VK_RETURN = 0x0D
_VK_F24 = 0x87  # ни к чему не привязана ни в одной программе
# Ctrl, Alt, левая и правая Win. Jarvis при печати их не нажимает: зажатая — значит, её держит человек.
_MODIFIER_VKS = (0x11, 0x12, 0x5B, 0x5C)
_GA_ROOTOWNER = 3

_ULONG_PTR = ctypes.c_size_t  # размер указателя: 8 байт в 64-битном Python

# Почему остановлен ввод — эти слова Jarvis произносит вслух.
WINDOW_CHANGED = "окно сменилось"
KEY_PRESSED = "нажата клавиша"
NO_WINDOW = "нет активного окна"


class InputInterrupted(OSError):
    """Ввод остановлен на полпути: человек переключил окно или держит Ctrl, Alt или Win."""

    def __init__(self, reason: str, typed: int | None = None, total: int | None = None) -> None:
        progress = f", напечатано {typed} из {total} символов" if total is not None else ""
        super().__init__(f"Ввод остановлен: {reason}{progress}")
        self.reason = reason
        self.typed = typed
        self.total = total


class _KeybdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _MouseInput(ctypes.Structure):
    # Нужен только ради размера: union INPUT определяется самым большим членом (MOUSEINPUT).
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeybdInput), ("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


def _load_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = wintypes.SHORT
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, _ULONG_PTR]
    user32.keybd_event.restype = None
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    return user32


_api = _load_user32()


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    pid: int


def _enum_windows() -> list[int]:
    handles: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _lparam):
        handles.append(int(hwnd))
        return True

    _api.EnumWindows(collect, 0)
    return handles


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def visible_windows() -> list[Window]:
    """Видимые окна верхнего уровня с заголовком."""
    found: list[Window] = []
    for hwnd in _enum_windows():
        if not _api.IsWindowVisible(hwnd):
            continue
        length = _api.GetWindowTextLengthW(hwnd)
        if not length:
            continue
        buffer = ctypes.create_unicode_buffer(length + 1)
        _api.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value not in _IGNORED_TITLES:
            found.append(Window(hwnd, buffer.value, _pid_of(hwnd)))
    return found


def process_windows(pid: int) -> list[int]:
    """Все окна верхнего уровня процесса, включая скрытые (например, окно программы в трее)."""
    return [hwnd for hwnd in _enum_windows() if _pid_of(hwnd) == pid]


def post_close(hwnd: int) -> None:
    # WM_CLOSE — как нажатие крестика: приложение само спросит про несохранённое.
    _api.PostMessageW(hwnd, _WM_CLOSE, 0, 0)


def foreground_window() -> int:
    return int(_api.GetForegroundWindow() or 0)


def activate(hwnd: int) -> bool:
    """Выводит окно на передний план. True — если оно действительно стало активным."""
    if _api.IsIconic(hwnd):
        _api.ShowWindow(hwnd, _SW_RESTORE)
    # Windows пускает SetForegroundWindow только процессу, недавно получившему ввод:
    # любое синтетическое нажатие снимает это ограничение. Alt для этого не годится: одиночный
    # Alt переводит окно в режим меню, и следующий напечатанный текст уходит в меню и кнопки.
    _api.keybd_event(_VK_F24, 0, 0, 0)
    _api.keybd_event(_VK_F24, 0, _KEYEVENTF_KEYUP, 0)
    _api.SetForegroundWindow(hwnd)
    for _ in range(5):
        if foreground_window() == hwnd:
            return True
        time.sleep(0.1)
    return foreground_window() == hwnd


def _key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> _Input:
    item = _Input(type=_INPUT_KEYBOARD)
    item.ki = _KeybdInput(vk, scan, flags, 0, 0)
    return item


def _is_extended(vk: int) -> bool:
    return vk in _EXTENDED_VKS


def _inject(items: list[_Input]) -> int:
    """Вставляет события в поток ввода; возвращает, сколько вставлено."""
    if not items:
        return 0
    array = (_Input * len(items))(*items)
    return _api.SendInput(len(items), array, ctypes.sizeof(_Input))


def _blocked(injected: int, total: int) -> OSError:
    # Чаще всего окно запущено от администратора, а Jarvis — нет: Windows (UIPI) глушит ввод.
    return OSError(f"SendInput вставил {injected} из {total} событий: ввод заблокирован")


def _send(items: list[_Input]) -> None:
    injected = _inject(items)
    if injected < len(items):
        raise _blocked(injected, len(items))


def _root(hwnd: int) -> int:
    # Корневой владелец: всплывающие окна (подсказки автодополнения, диалоги) считаются тем же окном.
    return int(_api.GetAncestor(hwnd, _GA_ROOTOWNER) or 0) if hwnd else 0


def _modifier_held() -> bool:
    # Старший бит GetAsyncKeyState — клавиша нажата прямо сейчас.
    return any(_api.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_VKS)


def _interruption(target_root: int) -> str | None:
    """Почему печатать дальше нельзя, или None — можно."""
    root = _root(foreground_window())
    if not root or root != target_root:
        return WINDOW_CHANGED
    if _modifier_held():
        # Например, левый Ctrl — клавиша записи: человек хочет перебить, а Enter превратился бы в Ctrl+Enter.
        return KEY_PRESSED
    return None


def _char_events(char: str) -> list[tuple[_Input, _Input]]:
    """Пары «нажать — отпустить» для одного символа."""
    if char == "\n":
        return [(_key_input(_VK_RETURN), _key_input(_VK_RETURN, flags=_KEYEVENTF_KEYUP))]
    # Символы вне BMP уходят суррогатной парой из двух UTF-16 единиц.
    data = char.encode("utf-16-le")
    units = [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]
    return [
        (
            _key_input(scan=unit, flags=_KEYEVENTF_UNICODE),
            _key_input(scan=unit, flags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP),
        )
        for unit in units
    ]


# Пауза между событиями ввода. Блокнот Windows 11 путает символы, если вставить текст одним
# пакетом (вместо «Привет, Ёжик!» получалось «Привет,» и ряд букв «а»): события идут по одному.
_TYPE_DELAY = 0.01


def send_text(text: str, target: int | None = None) -> None:
    """Печатает текст в окно target (по умолчанию — активное сейчас) как нажатия символов.

    Работает с любой раскладкой. Перед каждым символом проверяет, что активно всё то же окно
    и человек не держит Ctrl, Alt или Win; иначе останавливается — InputInterrupted.
    """
    chars = text.replace("\r", "")
    target_root = _root(foreground_window() if target is None else target)
    if not target_root:
        raise InputInterrupted(NO_WINDOW, 0, len(chars))
    for typed, char in enumerate(chars):
        reason = _interruption(target_root)
        if reason is not None:
            raise InputInterrupted(reason, typed, len(chars))
        for down, up in _char_events(char):
            _send([down])
            try:
                time.sleep(_TYPE_DELAY)
            finally:
                # Отпускаем, что бы ни случилось после нажатия: иначе клавиша (например, Enter) «залипнет».
                _send([up])
            time.sleep(_TYPE_DELAY)


def send_keys(vks: list[int]) -> None:
    """Нажимает клавиши по порядку и отпускает в обратном — так собираются сочетания вроде Ctrl+S.

    Если человек держит Ctrl, Alt или Win, не нажимает ничего: они сложились бы с нашими клавишами.
    """
    if _modifier_held():
        raise InputInterrupted(KEY_PRESSED)

    def flags(vk: int, extra: int = 0) -> int:
        return extra | (_KEYEVENTF_EXTENDEDKEY if _is_extended(vk) else 0)

    def releases(keys: list[int]) -> list[_Input]:
        return [_key_input(vk, flags=flags(vk, _KEYEVENTF_KEYUP)) for vk in reversed(keys)]

    # Одним пакетом: SendInput не перемешивает его с вводом человека.
    events = [_key_input(vk, flags=flags(vk)) for vk in vks] + releases(vks)
    injected = 0
    try:
        injected = _inject(events)
        if injected < len(events):
            raise _blocked(injected, len(events))
    finally:
        # Вставка оборвалась — отпускаем то, что успели нажать и не успели отпустить, иначе оно «залипнет».
        # Из первых injected событий нажатий min(injected, n), отпусканий max(0, injected - n).
        _send(releases(vks[: min(injected, 2 * len(vks) - injected)]))
