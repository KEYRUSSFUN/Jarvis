"""Окраска голоса под Джарвиса: металлический призвук и эхо небольшого помещения.

Считается numpy по готовой фразе за несколько миллисекунд, без новых зависимостей.
"""

import numpy as np

# Копии сигнала с задержкой в 1–4 мс, сложенные с ним, дают гребёнку резонансов —
# «звон», как у голоса из динамика. (задержка в мс, доля)
_METAL_TAPS = ((1.1, 0.6), (2.3, 0.45), (3.7, 0.3))


def _room_impulse(sample_rate: int, seconds: float = 0.4, decay: float = 0.07) -> np.ndarray:
    """Отклик небольшой комнаты: затухающий шум и несколько ранних отражений от стен."""
    rng = np.random.default_rng(7)  # одно и то же «помещение» при каждом запуске
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    impulse = rng.standard_normal(len(t)) * np.exp(-t / decay)
    for delay_ms, gain in ((11, 0.6), (17, 0.45), (23, 0.35)):
        impulse[int(sample_rate * delay_ms / 1000)] += gain * 8
    impulse[: int(sample_rate * 0.005)] = 0  # прямой звук уже есть в сухом сигнале
    return impulse / np.sqrt(np.sum(impulse**2))


def _convolve(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = len(a) + len(b) - 1
    size = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(a, size) * np.fft.rfft(b, size), size)[:n]


class VoiceEffects:
    """metal и room — доли эффекта; 0 выключает его."""

    def __init__(self, sample_rate: int, metal: float, room: float) -> None:
        self._metal = metal
        self._room = room
        self._taps = [(round(sample_rate * ms / 1000), gain) for ms, gain in _METAL_TAPS]
        self._impulse = _room_impulse(sample_rate)

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        if not self._metal and not self._room:
            return audio
        out = audio.astype(np.float64)
        if self._metal:
            for delay, gain in self._taps:
                out[delay:] += self._metal * gain * audio[: max(len(audio) - delay, 0)]
        if self._room:
            tail = np.zeros(len(self._impulse) - 1)
            out = np.concatenate([out, tail]) + self._room * _convolve(out, self._impulse)
        # Громкость как у исходной фразы: эффекты не должны ни глушить звук, ни перегружать его.
        peak = np.abs(out).max()
        if peak > 0:
            out *= np.abs(audio).max() / peak
        return out.astype(np.float32)
