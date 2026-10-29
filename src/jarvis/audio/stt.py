"""Распознавание речи faster-whisper: на видеокарте, а если CUDA недоступна — на CPU."""

import logging
import os
from pathlib import Path

import numpy as np

from jarvis.config import STTConfig

log = logging.getLogger(__name__)


def _add_cuda_libraries_to_path() -> None:
    """CUDA-библиотеки из пакетов nvidia-cublas-cu12 / nvidia-cudnn-cu12 (extra «gpu»)."""
    try:
        import nvidia
    except ImportError:
        return
    for package in ("cublas", "cudnn"):
        bin_dir = Path(nvidia.__path__[0]) / package / "bin"
        if bin_dir.is_dir():
            os.add_dll_directory(str(bin_dir))
            # CTranslate2 грузит часть DLL обычным LoadLibrary, которому нужен PATH.
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


class SpeechRecognizer:
    def __init__(self, config: STTConfig) -> None:
        from faster_whisper import WhisperModel

        self._config = config
        if config.device == "cuda":
            _add_cuda_libraries_to_path()
        try:
            self._model = WhisperModel(config.model, device=config.device, compute_type=config.compute_type)
            self._warm_up()
        except (RuntimeError, OSError) as exc:
            if config.device == "cpu":
                raise
            log.warning("Распознавание на %s не запустилось (%s), переключаюсь на CPU", config.device, exc)
            self._model = WhisperModel(config.model, device="cpu", compute_type="int8")

    def _warm_up(self) -> None:
        # Ошибки cuDNN и долгая инициализация ядер (~4 с) случаются при первом декодировании.
        # VAD вырезал бы тишину целиком и декодер бы не запустился, поэтому здесь он выключен;
        # segments — ленивый генератор, list() заставляет декодировать.
        silence = np.zeros(16000, dtype=np.float32)
        segments, _info = self._model.transcribe(silence, language=self._config.language, vad_filter=False)
        list(segments)
        # Модель VAD грузится при первом вызове с vad_filter=True (~0,5 с) — тоже заранее.
        segments, _info = self._model.transcribe(silence, language=self._config.language, vad_filter=True)
        list(segments)

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio,
            language=self._config.language,
            beam_size=5,
            hotwords=self._config.hotwords or None,
            # Встроенный Silero VAD режет тишину и спасает от «галлюцинаций» Whisper на паузах.
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
