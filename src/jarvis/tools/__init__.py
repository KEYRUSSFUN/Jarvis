import webbrowser
from datetime import datetime

from jarvis.config import Config
from jarvis.tools import apps, media, system, web
from jarvis.tools.registry import ToolRegistry


def build_registry(config: Config) -> ToolRegistry:
    registry = ToolRegistry()
    apps.register(registry, apps.WindowsApps(), config.apps.aliases)
    media.register(registry, media.SystemVolume(), media.press_media_key)
    web.register(registry, config.web.search_url, webbrowser.open)
    system.register(registry, system.run_command, datetime.now)
    return registry
