"""Цикл агента: LLM → инструменты → снова LLM, пока не будет готов ответ.

Агент помнит разговор: прошлые команды, вызовы инструментов и ответы уходят в LLM
вместе с новой командой. Ollama не пересчитывает уже обработанное начало переписки,
поэтому память почти не замедляет ответ. После паузы разговор начинается заново.
"""

import logging
import time
from collections.abc import Callable

from jarvis.audio.text_norm import SentenceSplitter
from jarvis.llm import ChatModel, TextDelta, ToolCall
from jarvis.safety import ToolExecutor
from jarvis.tools.registry import ToolRegistry, ToolResult

log = logging.getLogger(__name__)

NOT_UNDERSTOOD = "Простите, сэр, не понял команду."
GAVE_UP = "Простите, сэр, не получилось выполнить команду."


class Agent:
    def __init__(
        self,
        llm: ChatModel,
        registry: ToolRegistry,
        executor: ToolExecutor,
        system_prompt: str,
        max_steps: int = 5,
        memory_turns: int = 16,
        memory_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._executor = executor
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._memory_turns = memory_turns
        self._memory_seconds = memory_seconds
        self._clock = clock
        self._turns: list[list[dict]] = []  # каждая — сообщения одной команды: от вопроса до ответа
        self._last_command = float("-inf")

    def run(self, text: str, on_sentence: Callable[[str], None]) -> str:
        """Выполняет команду; каждое готовое предложение ответа сразу отдаёт в on_sentence."""
        if self._clock() - self._last_command > self._memory_seconds:
            self._turns.clear()
        history = [message for turn in self._turns for message in turn]
        messages = [{"role": "system", "content": self._system_prompt}, *history, {"role": "user", "content": text}]
        turn_start = len(messages) - 1

        answer = self._answer(messages, on_sentence)

        messages.append({"role": "assistant", "content": answer})
        self._turns.append(messages[turn_start:])
        if len(self._turns) > self._memory_turns:
            # Вытесняем сразу половину: пока начало переписки не меняется, Ollama берёт его из кэша,
            # а сдвиг на одну команду заставлял бы пересчитывать всю историю каждый раз.
            # Команды уходят целиком — обрывок с вызовом инструмента без ответа запутал бы модель.
            self._turns = self._turns[-(self._memory_turns // 2) :]
        self._last_command = self._clock()
        return answer

    def _answer(self, messages: list[dict], on_sentence: Callable[[str], None]) -> str:
        for _ in range(self._max_steps):
            content, calls = self._ask(messages, on_sentence)
            if not calls:
                if content.strip():
                    return content.strip()
                on_sentence(NOT_UNDERSTOOD)
                return NOT_UNDERSTOOD

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [{"function": {"name": c.name, "arguments": c.args}} for c in calls],
                }
            )
            results = [self._call_tool(call, messages) for call in calls]

            # Если каждый инструмент сам сказал, что сделал, — второй запрос к LLM не нужен.
            if all(result.speech for result in results):
                for result in results:
                    on_sentence(result.speech)
                return " ".join(result.speech for result in results)

        command = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        log.warning("Агент не уложился в %d шагов: %r", self._max_steps, command)
        on_sentence(GAVE_UP)
        return GAVE_UP

    def _ask(self, messages: list[dict], on_sentence: Callable[[str], None]) -> tuple[str, list[ToolCall]]:
        splitter = SentenceSplitter()
        content = ""
        calls: list[ToolCall] = []
        for event in self._llm.stream(messages, self._registry.schemas()):
            if isinstance(event, TextDelta):
                content += event.text
                for sentence in splitter.feed(event.text):
                    on_sentence(sentence)
            else:
                calls.append(event)
        tail = splitter.flush()
        if tail:
            on_sentence(tail)
        return content, calls

    def _call_tool(self, call: ToolCall, messages: list[dict]) -> ToolResult:
        result = self._executor.execute(call.name, call.args)
        log.info("Инструмент %s(%s) → %s", call.name, call.args, result.to_message())
        messages.append({"role": "tool", "tool_name": call.name, "content": result.to_message()})
        return result
