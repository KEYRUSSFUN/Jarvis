"""Цикл агента: LLM → инструменты → снова LLM, пока не будет готов ответ."""

import logging
from collections.abc import Callable

from jarvis.audio.text_norm import SentenceSplitter
from jarvis.llm import ChatModel, TextDelta, ToolCall
from jarvis.safety import ToolExecutor
from jarvis.tools.registry import ToolRegistry, ToolResult

log = logging.getLogger(__name__)

NOT_UNDERSTOOD = "Не понял команду."
GAVE_UP = "Не получилось выполнить команду."


class Agent:
    def __init__(
        self,
        llm: ChatModel,
        registry: ToolRegistry,
        executor: ToolExecutor,
        system_prompt: str,
        max_steps: int = 5,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._executor = executor
        self._system_prompt = system_prompt
        self._max_steps = max_steps

    def run(self, text: str, on_sentence: Callable[[str], None]) -> str:
        """Выполняет команду; каждое готовое предложение ответа сразу отдаёт в on_sentence."""
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": text},
        ]
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

        log.warning("Агент не уложился в %d шагов: %r", self._max_steps, text)
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
