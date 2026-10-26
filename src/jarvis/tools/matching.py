"""Нечёткий поиск названий: «хром» → «Google Chrome», «блокнот» → окно «Без имени — Блокнот».

Распознанная речь и ответ LLM могут быть и на русском, и на латинице,
поэтому сравниваем и как есть, и в транслитерации.
"""

import re

from rapidfuzz import fuzz

# Ниже этого балла совпадение считаем случайным: лучше сказать «не нашёл», чем открыть не то.
MIN_SCORE = 70

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}  # fmt: skip


def _transliterate(text: str) -> str:
    return "".join(_CYRILLIC_TO_LATIN.get(char, char) for char in text)


def _words_score(query: str, name: str) -> float:
    # Слово запроса сравниваем со словами названия целиком: частичное совпадение
    # внутри длинного слова давало ложные находки («хром» → «компьютером»).
    query_words, name_words = re.findall(r"\w+", query), re.findall(r"\w+", name)
    if not query_words or not name_words:
        return 0.0
    best = [max(fuzz.ratio(q, n) for n in name_words) for q in query_words]
    return sum(best) / len(best)


def _score(query: str, name: str) -> tuple[float, float]:
    """(совпадение по словам, сходство строк целиком) — второе решает при равенстве первого."""
    query, name = query.strip().lower(), name.strip().lower()
    pairs = [(query, name), (_transliterate(query), _transliterate(name))]
    return max(_words_score(q, n) for q, n in pairs), max(fuzz.ratio(q, n) for q, n in pairs)


def find_best(query: str, names: list[str], aliases: dict[str, str] | None = None) -> str | None:
    query = query.strip()
    if not query:
        return None

    target = {alias.lower(): name for alias, name in (aliases or {}).items()}.get(query.lower())
    if target in names:
        return target

    best_name, best_score = None, (0.0, 0.0)
    for name in names:
        score = _score(query, name)
        if score > best_score:
            best_name, best_score = name, score
    return best_name if best_score[0] >= MIN_SCORE else None
