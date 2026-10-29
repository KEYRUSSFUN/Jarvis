"""Настройки Jarvis из config.toml. Значения по умолчанию рассчитаны на RTX 4060 (8 ГБ)."""

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class AssistantConfig:
    hotkey: str = "ctrl_r"  # имя клавиши pynput: ctrl_r, f9, pause, …
    dry_run: bool = False  # инструменты только пишут в лог, ничего не делают
    max_steps: int = 5
    min_record_seconds: float = 0.3  # случайное короткое нажатие не считается командой
    memory_turns: int = 16  # сколько команд с ответами помнить; при переполнении остаётся половина
    memory_minutes: float = 5  # после такой паузы разговор начинается заново


@dataclass
class STTConfig:
    model: str = "large-v3-turbo"
    device: str = "cuda"  # при ошибке CUDA переключаемся на CPU
    compute_type: str = "int8_float16"
    language: str = "ru"
    hotwords: str = "Jarvis, Telegram, Chrome, YouTube, Spotify, Visual Studio Code"


@dataclass
class LLMConfig:
    model: str = "qwen3:8b"
    host: str = "http://127.0.0.1:11434"
    keep_alive: str = "30m"
    temperature: float = 0.2
    # Промпт с инструментами ~1400 токенов, остальное — память разговора. Контекст 4096 с обычным
    # кэшем вместе с Whisper не помещается в 8 ГБ: часть модели уходит в общую память, и ответ
    # замедляется в 2–5 раз. Поэтому Ollama запускается с 8-битным кэшем (см. README):
    # OLLAMA_KV_CACHE_TYPE=q8_0 и OLLAMA_FLASH_ATTENTION=1. Без них ставьте 2048.
    num_ctx: int = 4096


@dataclass
class TTSConfig:
    # Silero v5: в v5_cis_base 30 русских голосов. ru_alexandr — баритон около 120 Гц с самой
    # ровной интонацией из всех, ближе всего к Джарвису из «Железного человека».
    model_url: str = "https://models.silero.ai/models/tts/ru/v5_cis_base.pt"
    speaker: str = "ru_alexandr"
    pitch: str = "medium"  # SSML: x-low, low, medium, high, x-high
    rate: str = "medium"  # SSML: x-slow, slow, medium, fast, x-fast
    sample_rate: int = 48000
    metal: float = 0.35  # металлический призвук, 0 — выключен
    room: float = 0.25  # эхо небольшого помещения, 0 — выключено


@dataclass
class AppsConfig:
    aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class WebConfig:
    search_url: str = "https://yandex.ru/search/?text={query}"


@dataclass
class Config:
    assistant: AssistantConfig = field(default_factory=AssistantConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    apps: AppsConfig = field(default_factory=AppsConfig)
    web: WebConfig = field(default_factory=WebConfig)


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    with path.open("rb") as file:
        raw = tomllib.load(file)

    sections = {f.name: f.default_factory for f in fields(Config)}
    unknown = set(raw) - set(sections)
    if unknown:
        raise ValueError(f"{path}: неизвестные разделы {sorted(unknown)}")

    config = Config()
    for name, values in raw.items():
        section_type = sections[name]
        allowed = {f.name for f in fields(section_type)}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"{path}: в разделе [{name}] неизвестные ключи {sorted(unknown)}")
        setattr(config, name, section_type(**values))
    return config
