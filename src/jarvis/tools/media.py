"""Громкость системы и управление плеером через медиаклавиши."""

import ctypes
from collections.abc import Callable
from typing import Protocol

from jarvis.tools.registry import Param, ToolRegistry, ToolResult

_MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}


class VolumeControl(Protocol):
    def get(self) -> float: ...
    def set(self, level: float) -> None: ...
    def set_mute(self, muted: bool) -> None: ...


def _clamp_percent(percent: int) -> int:
    return max(0, min(100, percent))


def register(registry: ToolRegistry, volume: VolumeControl, press_key: Callable[[int], None]) -> None:
    def apply(percent: int) -> ToolResult:
        percent = _clamp_percent(percent)
        volume.set(percent / 100)
        volume.set_mute(False)
        return ToolResult(ok=True, data={"volume": percent}, speech=f"Громкость {percent}%.")

    @registry.tool(
        description=(
            "Установить громкость на названное значение в процентах. "
            "Примеры: «громкость 50», «громкость на тридцать», «громкость 75», «на максимум» — 100."
        ),
        params={"percent": Param("integer", "Громкость от 0 до 100")},
    )
    def set_volume(percent: int) -> ToolResult:
        return apply(percent)

    @registry.tool(
        description=(
            "Сделать громче или тише относительно текущей громкости — только когда итоговое значение не названо. "
            "«Погромче» — +10, «потише» — −10, «тише на 20» — −20."
        ),
        params={"delta": Param("integer", "Изменение в процентах: положительное — громче, отрицательное — тише")},
    )
    def change_volume(delta: int) -> ToolResult:
        return apply(round(volume.get() * 100) + delta)

    # Два инструмента без аргументов вместо одного с флагом: маленькие модели теряют булевы аргументы.
    @registry.tool(description="Выключить звук системы. Примеры: «выключи звук», «без звука», «замолчи, компьютер».")
    def mute() -> ToolResult:
        volume.set_mute(True)
        return ToolResult(ok=True, speech="Звук выключен.")

    @registry.tool(description="Включить звук системы после выключения. Примеры: «включи звук», «верни звук».")
    def unmute() -> ToolResult:
        volume.set_mute(False)
        return ToolResult(ok=True, speech="Звук включён.")

    @registry.tool(
        description=(
            "Управление музыкой или видео. play_pause — «пауза», «продолжи», «включи дальше»; "
            "next — «следующий трек»; previous — «предыдущий»; stop — «стоп музыка»."
        ),
        params={"action": Param("string", "Что сделать", enum=list(_MEDIA_KEYS))},
    )
    def media_control(action: str) -> ToolResult:
        if action not in _MEDIA_KEYS:
            return ToolResult(ok=False, error=f"Неизвестное действие {action}")
        press_key(_MEDIA_KEYS[action])
        return ToolResult(ok=True, speech="Готово.")


class SystemVolume:
    """Громкость устройства вывода по умолчанию через Core Audio (pycaw)."""

    @staticmethod
    def _endpoint():
        from pycaw.pycaw import AudioUtilities

        # Берём устройство заново: пользователь мог переключиться на наушники.
        return AudioUtilities.GetSpeakers().EndpointVolume

    def get(self) -> float:
        return self._endpoint().GetMasterVolumeLevelScalar()

    def set(self, level: float) -> None:
        self._endpoint().SetMasterVolumeLevelScalar(level, None)

    def set_mute(self, muted: bool) -> None:
        self._endpoint().SetMute(int(muted), None)


def press_media_key(vk: int) -> None:
    keyeventf_extendedkey, keyeventf_keyup = 0x1, 0x2
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, keyeventf_extendedkey, 0)
    user32.keybd_event(vk, 0, keyeventf_extendedkey | keyeventf_keyup, 0)
