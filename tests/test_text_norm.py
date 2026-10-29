import pytest

from jarvis.audio.text_norm import SentenceSplitter, normalize_for_speech


def feed_all(splitter: SentenceSplitter, deltas: list[str]) -> list[str]:
    sentences = []
    for delta in deltas:
        sentences.extend(splitter.feed(delta))
    tail = splitter.flush()
    if tail:
        sentences.append(tail)
    return sentences


class TestSentenceSplitter:
    def test_emits_sentence_as_soon_as_it_ends(self):
        splitter = SentenceSplitter()
        assert splitter.feed("Открываю брау") == []
        assert splitter.feed("зер. Что ещё") == ["Открываю браузер."]

    def test_flush_returns_unfinished_tail(self):
        assert feed_all(SentenceSplitter(), ["Готово! Громкость ", "пятьдесят"]) == [
            "Готово!",
            "Громкость пятьдесят",
        ]

    def test_does_not_split_decimal_point(self):
        assert feed_all(SentenceSplitter(), ["Версия 3.5 установлена. Всё."]) == [
            "Версия 3.5 установлена.",
            "Всё.",
        ]

    def test_splits_on_question_ellipsis_and_newline(self):
        assert feed_all(SentenceSplitter(), ["Открыть? Ну… ладно\nСделано"]) == [
            "Открыть?",
            "Ну…",
            "ладно",
            "Сделано",
        ]

    def test_flush_of_whitespace_returns_none(self):
        splitter = SentenceSplitter()
        splitter.feed("Да. ")
        assert splitter.flush() is None


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Громкость 5", "Громкость пять"),
        ("Громкость 45%", "Громкость сорок пять процентов"),
        ("Заряд 1%", "Заряд один процент"),
        ("Заряд 22%", "Заряд двадцать два процента"),
        ("Сейчас 14:05", "Сейчас четырнадцать ноль пять"),
        ("Сейчас 9:30", "Сейчас девять тридцать"),
        ("Занято 3,5 гигабайта", "Занято три целых пять десятых гигабайта"),
        ("**Готово**, `ok`", "Готово, ок"),
        ("Открываю Telegram", "Открываю телеграм"),
        ("Включаю YouTube", "Включаю ютубе"),
    ],
)
def test_normalize_for_speech(text, spoken):
    assert normalize_for_speech(text) == spoken
