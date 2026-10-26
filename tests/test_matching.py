import pytest

from jarvis.tools.matching import find_best

START_MENU = [
    "Telegram",
    "Google Chrome",
    "Microsoft Edge",
    "Microsoft Store",
    "Блокнот",
    "Калькулятор",
    "Visual Studio Code",
    "Spotify",
    "Параметры",
]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Telegram", "Telegram"),
        ("телеграм", "Telegram"),
        ("chrome", "Google Chrome"),
        ("хром", "Google Chrome"),
        ("гугл хром", "Google Chrome"),
        ("edge", "Microsoft Edge"),
        ("блокнот", "Блокнот"),
        ("Калькулятор", "Калькулятор"),
        ("visual studio", "Visual Studio Code"),
        ("спотифай", "Spotify"),
        ("настройки", None),
        ("фотошоп", None),
        ("", None),
    ],
)
def test_finds_start_menu_entry(query, expected):
    assert find_best(query, START_MENU) == expected


def test_alias_wins_over_fuzzy_match():
    assert find_best("телега", START_MENU, aliases={"телега": "Telegram"}) == "Telegram"


def test_alias_is_case_insensitive():
    assert find_best("Настройки", START_MENU, aliases={"настройки": "Параметры"}) == "Параметры"


def test_alias_to_missing_entry_falls_back_to_fuzzy():
    assert find_best("хром", START_MENU, aliases={"хром": "Chromium"}) == "Google Chrome"


# Часть настоящего меню «Пуск»: здесь нет Chrome, и короткий запрос не должен
# цепляться за кусок длинного названия («хром» ≠ «Управление компьютером»).
REAL_START_MENU = [
    "Управление компьютером",
    "Просмотр событий",
    "Node.js command prompt",
    "Языковые параметры Office",
    "Настройки",
    "Microsoft Edge",
    "Яндекс Браузер с Алисой AI",
    "Яндекс.Музыка",
    "Discord",
    "Проводник",
]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("хром", None),
        ("настройки", "Настройки"),
        ("яндекс музыка", "Яндекс.Музыка"),
        ("музыку", "Яндекс.Музыка"),
        ("браузер", "Яндекс Браузер с Алисой AI"),
        ("дискорд", "Discord"),
        ("проводник", "Проводник"),
    ],
)
def test_real_start_menu(query, expected):
    assert find_best(query, REAL_START_MENU) == expected


def test_equal_word_match_prefers_name_closest_to_query():
    names = ["Языковые параметры Office", "Параметры"]
    assert find_best("параметры", names) == "Параметры"


WINDOWS = ["Без имени — Блокнот", "Telegram", "jarvis — Visual Studio Code", "YouTube — Google Chrome"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("блокнот", "Без имени — Блокнот"),
        ("хром", "YouTube — Google Chrome"),
        ("телеграм", "Telegram"),
        ("калькулятор", None),
    ],
)
def test_finds_window_by_title(query, expected):
    assert find_best(query, WINDOWS) == expected
