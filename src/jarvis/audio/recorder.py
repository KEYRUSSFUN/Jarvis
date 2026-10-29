"""Запись команды, пока зажата горячая клавиша (push-to-talk)."""

import logging
import queue
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd
from pynput import keyboard

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # faster-whisper ждёт 16 кГц моно


class UtteranceBuffer:
    """Копит звук между нажатием и отпусканием клавиши. Потокобезопасен."""

    def __init__(self, sample_rate: int, min_seconds: float) -> None:
        self._min_samples = int(sample_rate * min_seconds)
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._recording = False

    def start(self) -> None:
        with self._lock:
            if not self._recording:
                self._chunks = []
                self._recording = True

    def add(self, chunk: np.ndarray) -> None:
        with self._lock:
            if self._recording:
                # sounddevice переиспользует свой буфер — храним копию.
                self._chunks.append(chunk.copy())

    def stop(self) -> np.ndarray | None:
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            chunks, self._chunks = self._chunks, []
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        return audio if audio.size >= self._min_samples else None


def parse_key(name: str) -> keyboard.Key | keyboard.KeyCode:
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return keyboard.Key[name]
    except KeyError:
        raise ValueError(f"Неизвестная клавиша {name!r}: нужно имя из pynput.keyboard.Key, например ctrl_r или f9") from None


class PushToTalk:
    """Микрофон открыт всё время, но звук сохраняется только пока зажата клавиша."""

    def __init__(self, hotkey: str, min_seconds: float, on_press: Callable[[], None] = lambda: None) -> None:
        self._key = parse_key(hotkey)
        self._on_press_hook = on_press
        self._buffer = UtteranceBuffer(SAMPLE_RATE, min_seconds)
        self._utterances: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=1600, callback=self._on_audio
        )
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)

    def start(self) -> None:
        self._stream.start()
        self._listener.start()

    def close(self) -> None:
        self._listener.stop()
        self._stream.close()

    def record(self, timeout: float | None = None) -> np.ndarray | None:
        """Ждёт следующую фразу: нажатие → речь → отпускание. None — если не дождались."""
        try:
            return self._utterances.get(timeout=timeout)
        except queue.Empty:
            return None

    def discard_pending(self) -> None:
        """Выбрасывает фразы, записанные раньше, — чтобы старая команда не стала ответом на новый вопрос."""
        while True:
            try:
                self._utterances.get_nowait()
            except queue.Empty:
                return

    def _on_audio(self, indata: np.ndarray, frames: int, time, status: sd.CallbackFlags) -> None:
        if status:
            log.debug("Микрофон: %s", status)
        self._buffer.add(indata[:, 0])

    def _on_press(self, key, injected: bool = False) -> None:
        # injected — нажатие сделала программа (в т. ч. сам Джарвис через SendInput), а не человек.
        if not injected and key == self._key:
            self._buffer.start()
            self._on_press_hook()

    def _on_release(self, key, injected: bool = False) -> None:
        if not injected and key == self._key:
            audio = self._buffer.stop()
            if audio is not None:
                self._utterances.put(audio)
