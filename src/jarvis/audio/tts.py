"""Озвучка ответов: предложения синтезируются и играются по очереди в отдельном потоке."""

import logging
import queue
import re
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import numpy as np
import sounddevice as sd

from jarvis.audio.text_norm import normalize_for_speech

log = logging.getLogger(__name__)


class Player(Protocol):
    def play(self, audio: Any) -> None: ...
    def stop(self) -> None: ...


class Speaker:
    """Очередь фраз: пока играет первая, следующая уже ждёт. stop() обрывает речь и очищает очередь."""

    def __init__(self, synthesize: Callable[[str], Any], player: Player) -> None:
        self._synthesize = synthesize
        self._player = player
        self._queue: queue.Queue[tuple[int, str]] = queue.Queue()
        self._generation = 0  # stop() увеличивает его, и устаревшие фразы пропускаются
        threading.Thread(target=self._work, name="speaker", daemon=True).start()

    def say(self, text: str) -> None:
        text = normalize_for_speech(text)
        if re.search(r"[^\W\d_]", text):
            self._queue.put((self._generation, text))

    def stop(self) -> None:
        self._generation += 1
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            self._queue.task_done()
        self._player.stop()

    def wait(self) -> None:
        self._queue.join()

    def _work(self) -> None:
        while True:
            generation, text = self._queue.get()
            try:
                if generation == self._generation:
                    audio = self._synthesize(text)
                    if generation == self._generation:
                        self._player.play(audio)
            except Exception:
                log.exception("Не удалось озвучить %r", text)
            finally:
                self._queue.task_done()


class SileroSynthesizer:
    """Silero TTS на CPU. Модель скачивается один раз в models/."""

    def __init__(self, model_url: str, speaker: str, sample_rate: int, cache_dir: Path) -> None:
        import torch

        path = cache_dir / Path(urlparse(model_url).path).name
        if not path.exists():
            log.info("Скачиваю модель озвучки %s", model_url)
            cache_dir.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(model_url, path)
        torch.set_num_threads(4)
        self._model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
        self._model.to(torch.device("cpu"))
        self._speaker = speaker
        self.sample_rate = sample_rate

    def __call__(self, text: str) -> np.ndarray:
        audio = self._model.apply_tts(
            text=text, speaker=self._speaker, sample_rate=self.sample_rate, put_accent=True, put_yo=True
        )
        return audio.numpy()


class SoundDevicePlayer:
    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate

    def play(self, audio: np.ndarray) -> None:
        sd.play(audio, self._sample_rate)
        sd.wait()

    def stop(self) -> None:
        sd.stop()
