import threading

import numpy as np
import pytest

from jarvis.audio.recorder import UtteranceBuffer
from jarvis.audio.tts import Speaker
from jarvis.dialog import is_confirmation


def chunk(value: float, samples: int = 1600) -> np.ndarray:
    return np.full(samples, value, dtype=np.float32)


class TestUtteranceBuffer:
    def test_collects_audio_between_start_and_stop(self):
        buffer = UtteranceBuffer(sample_rate=16000, min_seconds=0.1)
        buffer.add(chunk(9.0))  # до нажатия — не записываем
        buffer.start()
        buffer.add(chunk(1.0))
        buffer.add(chunk(2.0))
        audio = buffer.stop()
        assert audio.tolist() == [1.0] * 1600 + [2.0] * 1600

    def test_key_autorepeat_does_not_restart_recording(self):
        buffer = UtteranceBuffer(sample_rate=16000, min_seconds=0.1)
        buffer.start()
        buffer.add(chunk(1.0))
        buffer.start()  # Windows шлёт повторные нажатия, пока клавиша зажата
        buffer.add(chunk(2.0))
        assert buffer.stop().size == 3200

    def test_too_short_press_gives_nothing(self):
        buffer = UtteranceBuffer(sample_rate=16000, min_seconds=0.3)
        buffer.start()
        buffer.add(chunk(1.0, samples=4000))  # 0.25 с
        assert buffer.stop() is None

    def test_stop_without_start_gives_nothing(self):
        assert UtteranceBuffer(sample_rate=16000, min_seconds=0.1).stop() is None

    def test_buffer_is_empty_for_next_utterance(self):
        buffer = UtteranceBuffer(sample_rate=16000, min_seconds=0.1)
        buffer.start()
        buffer.add(chunk(1.0))
        buffer.stop()
        buffer.start()
        buffer.add(chunk(2.0))
        assert set(buffer.stop().tolist()) == {2.0}


class RecordingPlayer:
    """Подмена колонок: запоминает, что сыграли, и умеет «зависнуть» на первой фразе."""

    def __init__(self, block_first: bool = False) -> None:
        self.played: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()
        if not block_first:
            self.release.set()

    def play(self, audio: str) -> None:
        self.started.set()
        self.release.wait(timeout=5)
        self.played.append(audio)

    def stop(self) -> None:
        self.release.set()


class TestSpeaker:
    def test_speaks_sentences_in_order_with_normalized_text(self):
        player = RecordingPlayer()
        speaker = Speaker(synthesize=lambda text: f"<{text}>", player=player)
        speaker.say("Громкость 40%.")
        speaker.say("Готово.")
        speaker.wait()
        assert player.played == ["<Громкость сорок процентов.>", "<Готово.>"]

    def test_stop_drops_queued_sentences(self):
        player = RecordingPlayer(block_first=True)
        speaker = Speaker(synthesize=lambda text: text, player=player)
        speaker.say("Первая.")
        speaker.say("Вторая.")
        speaker.say("Третья.")
        assert player.started.wait(timeout=5)
        speaker.stop()
        speaker.wait()
        assert player.played == ["Первая."]

    def test_text_without_letters_is_not_spoken(self):
        player = RecordingPlayer()
        speaker = Speaker(synthesize=lambda text: text, player=player)
        speaker.say("…")
        speaker.say("Да.")
        speaker.wait()
        assert player.played == ["Да."]


@pytest.mark.parametrize(
    ("answer", "confirmed"),
    [
        ("Да.", True),
        ("Подтверждаю", True),
        ("да, выключай", True),
        ("Давай.", True),
        ("Нет.", False),
        ("Не надо", False),
        ("Отмена!", False),
        ("да нет, не надо", False),
        ("", False),
        ("что?", False),
        ("Дания", False),
    ],
)
def test_voice_confirmation(answer, confirmed):
    assert is_confirmation(answer) is confirmed
