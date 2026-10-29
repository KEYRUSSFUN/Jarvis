"""Потоковый доступ к LLM: ответ приходит кусками текста и вызовами инструментов."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import ollama


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


LLMEvent = TextDelta | ToolCall


class ChatModel(Protocol):
    def stream(self, messages: list[dict], tools: list[dict]) -> Iterator[LLMEvent]: ...


class OllamaChat:
    def __init__(self, model: str, host: str, keep_alive: str, temperature: float, num_ctx: int) -> None:
        self._client = ollama.Client(host=host)
        self._model = model
        self._keep_alive = keep_alive
        self._options = {"temperature": temperature, "num_ctx": num_ctx}

    def stream(self, messages: list[dict], tools: list[dict]) -> Iterator[LLMEvent]:
        chunks = self._client.chat(
            model=self._model,
            messages=messages,
            tools=tools,
            stream=True,
            think=False,  # рассуждения Qwen3 добавляют секунды задержки
            keep_alive=self._keep_alive,
            options=self._options,
        )
        for chunk in chunks:
            if chunk.message.content:
                yield TextDelta(chunk.message.content)
            for call in chunk.message.tool_calls or []:
                yield ToolCall(call.function.name, dict(call.function.arguments or {}))

    def warm_up(self, messages: list[dict] | None = None, tools: list[dict] | None = None) -> None:
        """Загружает модель в видеопамять заранее, чтобы первая команда не ждала.

        Если передать системный промпт и инструменты, Ollama обработает их сейчас и сохранит
        в кэше: первая обработка длинного промпта после загрузки занимает ~5 с.
        """
        self._client.chat(
            model=self._model,
            messages=messages or [],
            tools=tools,
            think=False,
            keep_alive=self._keep_alive,
            options={**self._options, "num_predict": 1},
        )
