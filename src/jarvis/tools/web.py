"""Открытие сайтов и поиск в браузере по умолчанию."""

from collections.abc import Callable
from urllib.parse import quote_plus, urlparse

from jarvis.tools.registry import Param, ToolRegistry, ToolResult


def register(registry: ToolRegistry, search_url: str, open_in_browser: Callable[[str], object]) -> None:
    @registry.tool(
        description="Открыть сайт в браузере.",
        params={"url": Param("string", "Адрес сайта, например youtube.com")},
    )
    def open_url(url: str) -> ToolResult:
        if "://" not in url:
            url = "https://" + url
        open_in_browser(url)
        return ToolResult(ok=True, speech=f"Открываю {urlparse(url).hostname}.")

    @registry.tool(
        description="Найти что-то в интернете: открывает страницу поиска в браузере.",
        params={"query": Param("string", "Поисковый запрос")},
    )
    def web_search(query: str) -> ToolResult:
        open_in_browser(search_url.format(query=quote_plus(query)))
        return ToolResult(ok=True, speech=f"Ищу: {query}.")
