"""Главный цикл: клавиша → запись → распознавание → агент → озвучка."""

import argparse
import logging
import time
from pathlib import Path

from jarvis.agent import Agent
from jarvis.audio.recorder import PushToTalk
from jarvis.audio.stt import SpeechRecognizer
from jarvis.audio.tts import SileroSynthesizer, SoundDevicePlayer, Speaker
from jarvis.config import load_config
from jarvis.dialog import is_confirmation
from jarvis.llm import OllamaChat
from jarvis.prompts import SYSTEM_PROMPT
from jarvis.safety import ToolExecutor
from jarvis.tools import build_registry

log = logging.getLogger("jarvis")

PROJECT_DIR = Path(__file__).resolve().parents[2]
CONFIRM_TIMEOUT_SECONDS = 20  # нет ответа — считаем отказом


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_dir / "jarvis.log", encoding="utf-8")],
    )
    for noisy in ("httpx", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis — голосовой ассистент")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "config.toml")
    parser.add_argument("--dry-run", action="store_true", help="инструменты только пишут в лог")
    args = parser.parse_args()

    setup_logging(PROJECT_DIR / "logs")
    config = load_config(args.config)
    dry_run = args.dry_run or config.assistant.dry_run

    log.info("Загружаю модели…")
    synthesizer = SileroSynthesizer(
        config.tts.model_url, config.tts.speaker, config.tts.sample_rate, PROJECT_DIR / "models"
    )
    speaker = Speaker(synthesizer, SoundDevicePlayer(config.tts.sample_rate))
    recognizer = SpeechRecognizer(config.stt)
    llm = OllamaChat(config.llm.model, config.llm.host, config.llm.keep_alive, config.llm.temperature)
    llm.warm_up()

    # Нажатие клавиши обрывает речь Jarvis — можно перебить его новой командой.
    push_to_talk = PushToTalk(config.assistant.hotkey, config.assistant.min_record_seconds, on_press=speaker.stop)

    def confirm(prompt: str) -> bool:
        speaker.say(prompt)
        speaker.say("Скажите «да» или «нет».")
        speaker.wait()
        push_to_talk.discard_pending()
        audio = push_to_talk.record(timeout=CONFIRM_TIMEOUT_SECONDS)
        answer = recognizer.transcribe(audio) if audio is not None else ""
        log.info("Подтверждение «%s» → «%s»", prompt, answer)
        return is_confirmation(answer)

    registry = build_registry(config)
    executor = ToolExecutor(registry, confirm, dry_run=dry_run)
    agent = Agent(llm, registry, executor, SYSTEM_PROMPT, max_steps=config.assistant.max_steps)

    push_to_talk.start()
    mode = " (dry-run: команды не выполняются)" if dry_run else ""
    log.info("Готов. Держите %s и говорите команду%s. Выход — Ctrl+C.", config.assistant.hotkey, mode)
    speaker.say("Jarvis на связи.")
    try:
        while True:
            audio = push_to_talk.record()
            released = time.perf_counter()
            text = recognizer.transcribe(audio)
            recognized = time.perf_counter()
            log.info("Команда: %r (распознавание %.0f мс)", text, (recognized - released) * 1000)
            if not text:
                continue

            first_sentence: list[float] = []

            def say(sentence: str) -> None:
                if not first_sentence:
                    first_sentence.append(time.perf_counter())
                speaker.say(sentence)

            answer = agent.run(text, say)
            if first_sentence:
                log.info("Ответ: %r (до первой фразы %.0f мс)", answer, (first_sentence[0] - released) * 1000)
    except KeyboardInterrupt:
        log.info("Выход")
    finally:
        push_to_talk.close()
