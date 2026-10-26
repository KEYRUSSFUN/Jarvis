"""Подготовка текста к озвучке: разбиение на предложения и запись чисел словами.

Silero TTS не читает цифры и латиницу, поэтому всё, что уходит в синтез,
сначала проходит через normalize_for_speech.
"""

import re

from num2words import num2words

_SENTENCE_END = ".!?…"


class SentenceSplitter:
    """Собирает потоковый ответ LLM и отдаёт законченные предложения по мере готовности."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        text = self._buffer
        sentences = []
        start = 0
        i = 0
        while i < len(text):
            char = text[i]
            if char == "\n":
                end, cut = i, i + 1
            elif char in _SENTENCE_END:
                j = i
                while j + 1 < len(text) and text[j + 1] in _SENTENCE_END:
                    j += 1
                # Точка внутри «3.5» не конец предложения: после конца нужен пробел.
                if j + 1 >= len(text) or not text[j + 1].isspace():
                    i = j + 1
                    continue
                end = cut = j + 1
            else:
                i += 1
                continue
            sentence = text[start:end].strip()
            if sentence:
                sentences.append(sentence)
            start = i = cut
        self._buffer = text[start:]
        return sentences

    def flush(self) -> str | None:
        tail = self._buffer.strip()
        self._buffer = ""
        return tail or None


_LATIN_DIGRAPHS = {"you": "ю", "ch": "ч", "sh": "ш", "zh": "ж", "kh": "х", "ph": "ф", "th": "т", "oo": "у", "ee": "и"}
_LATIN_LETTERS = dict(
    zip(
        "abcdefghiklmnopqrstuvwyz",
        ["а", "б", "к", "д", "е", "ф", "г", "х", "и", "к", "л", "м", "н", "о", "п", "к", "р", "с", "т", "у", "в", "в", "и", "з"],
    )
)
_LATIN_LETTERS |= {"j": "дж", "x": "кс"}
_LATIN_TOKEN = re.compile("|".join(sorted(_LATIN_DIGRAPHS, key=len, reverse=True)) + "|[a-z]")


def _transliterate_word(match: re.Match) -> str:
    word = match.group().lower()
    return _LATIN_TOKEN.sub(lambda m: _LATIN_DIGRAPHS.get(m.group()) or _LATIN_LETTERS[m.group()], word)


def _percent_word(number: str) -> str:
    if not number.isdigit():
        return "процента"
    n = int(number)
    if 11 <= n % 100 <= 14:
        return "процентов"
    if n % 10 == 1:
        return "процент"
    if 2 <= n % 10 <= 4:
        return "процента"
    return "процентов"


def _number_words(number: str) -> str:
    if number.isdigit():
        return num2words(int(number), lang="ru")
    return num2words(float(number.replace(",", ".")), lang="ru")


def _time_words(match: re.Match) -> str:
    hours, minutes = match.group(1), match.group(2)
    minutes_words = num2words(int(minutes), lang="ru")
    if minutes.startswith("0"):
        minutes_words = "ноль " + minutes_words
    return f"{num2words(int(hours), lang='ru')} {minutes_words}"


def normalize_for_speech(text: str) -> str:
    text = re.sub(r"[*#`]", "", text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _time_words, text)
    text = re.sub(
        r"(\d+(?:[.,]\d+)?)\s*%",
        lambda m: f"{_number_words(m.group(1))} {_percent_word(m.group(1))}",
        text,
    )
    text = re.sub(r"\d+(?:[.,]\d+)?", lambda m: _number_words(m.group()), text)
    text = re.sub(r"[A-Za-z]+", _transliterate_word, text)
    return re.sub(r"\s+", " ", text).strip()
