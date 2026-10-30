"""Автоматизация браузера: чтение страниц, клики, заполнение форм.

Чтение страниц работает через стандартную библиотеку (urllib + HTMLParser)
и не требует дополнительных зависимостей.
Клики по элементам в окне браузера реализованы через Microsoft UI Automation
(comtypes уже в зависимостях). Для полноценной работы с DOM
понадобятся Selenium или Playwright — не в текущем scope.
"""

import logging
import re
import urllib.request
from html.parser import HTMLParser
from typing import Protocol

from jarvis.tools import winapi
from jarvis.tools.matching import find_best
from jarvis.tools.registry import Param, Risk, ToolRegistry, ToolResult

log = logging.getLogger(__name__)

# Максимальная длина текста страницы, которая отдаётся в ответ (для речи).
_MAX_PAGE_TEXT_CHARS = 1500


class _PageTextExtractor(HTMLParser):
    """Извлекает читаемый текст из HTML: заголовки, параграфы, ссылки, статьи."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False  # внутри script/style — пропускаем
        self._block_tags = {
            "p",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "article",
            "main",
            "section",
            "li",
            "br",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip = True
        elif tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False
        elif tag in self._block_tags:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


def _fetch_page_text(url: str) -> str:
    """Скачивает страницу и возвращает извлечённый текст (обрезанный)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis/0.1"},
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read(200_000)  # не более 200 КБ
    charset = response.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, errors="replace")
    extractor = _PageTextExtractor()
    extractor.feed(html)
    text = extractor.text()
    return text[:_MAX_PAGE_TEXT_CHARS]


class BrowserBackend(Protocol):
    def find_window(self, title: str | None) -> int | None: ...


class WindowsBrowserBackend:
    """Ищет окно браузера по заголовку через существующий winapi."""

    def find_window(self, title: str | None) -> int | None:
        if not title:
            return None
        windows = winapi.visible_windows()
        titles = [w.title for w in windows]
        match = find_best(title, titles)
        if match is None:
            return None
        for w in windows:
            if w.title == match:
                return w.hwnd
        return None


def _click_ui_element(hwnd: int, name: str) -> None:
    """Клик по элементу с заданным именем в окне hwnd через UI Automation.

    Требует comtypes и Microsoft UI Automation. Подробные GUID и интерфейсы
    могут потребовать наровки под конкретную версию Windows.
    """
    import comtypes.client  # type: ignore

    # CLSID_CUIAutomation — объект Microsoft UI Automation.
    automation = comtypes.client.CreateObject(
        "{FF48DBA4-60F3-403B-A798-7F6D6E6E6E6E}",
        interface="IUIAutomation",
    )
    root = automation.ElementFromHandle(hwnd)
    # UIA_NamePropertyId = 30000, TreeScope_Descendants = 0x20000
    condition = automation.CreatePropertyCondition(30000, name)
    element = root.FindFirst(0x20000, condition)
    if element is None:
        raise ValueError(f"Элемент «{name}» не найден в окне")
    # UIA_InvokePatternId = 10000 — нажатие на элемент
    pattern = element.GetCurrentPattern(10000)
    pattern.Invoke()


def register(registry: ToolRegistry) -> None:
    backend = WindowsBrowserBackend()

    @registry.tool(
        description="Прочитать текст веб-страницы по адресу URL. Возвращает основное содержимое страницы.",
        params={"url": Param("string", "Адрес страницы, например https://yandex.ru")},
        risk=Risk.SAFE,
    )
    def read_page(url: str) -> ToolResult:
        if "://" not in url:
            url = "https://" + url
        try:
            text = _fetch_page_text(url)
        except Exception as exc:
            log.exception("Не удалось прочитать страницу %s", url)
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        if not text:
            return ToolResult(ok=False, error="На странице не найдено читаемого текста")
        return ToolResult(ok=True, data={"url": url, "text": text}, speech=text)

    @registry.tool(
        description="Кликнуть по элементу на странице в окне браузера. Ищет окно браузера по заголовку и элемент по названию.",
        params={
            "window": Param(
                "string", "Заголовок окна браузера, например «Яндекс» или «Google Chrome»", required=False
            ),
            "element": Param("string", "Название кнопки, ссылки или поля ввода"),
        },
        risk=Risk.SAFE,
    )
    def click_browser_element(window: str | None = None, element: str = "") -> ToolResult:
        hwnd = backend.find_window(window)
        if hwnd is None:
            return ToolResult(ok=False, error=f"Окно браузера «{window}» не найдено")
        try:
            _click_ui_element(hwnd, element)
        except NotImplementedError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:
            log.exception("Клик по элементу %r не удался", element)
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(ok=True, speech=f"Кликнул по {element}.")