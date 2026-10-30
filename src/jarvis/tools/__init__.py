import webbrowser
from datetime import datetime

from jarvis.config import Config
from jarvis.tools import apps, files, keys, media, processes, system, web
from jarvis.tools.browser_automation import register as register_browser
from jarvis.tools.ui_automation import register as register_ui
from jarvis.tools.registry import ToolRegistry


def build_registry(config: Config, preload: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    windows_apps = apps.WindowsApps()
    if preload:
        windows_apps.preload()
    launched = files.LaunchLog()
    windows_files = files.WindowsFiles()
    windows_processes = processes.WindowsProcesses()
    apps.register(
        registry,
        windows_apps,
        config.apps.aliases,
        files=windows_files,
        launched=launched,
        # Защищённые pid считаем в момент закрытия: это дёшево, а набор родителей может смениться.
        close_background=lambda app: processes.close_program(app, windows_processes, processes.protected_pids()),
    )
    files.register(registry, windows_files, launched)
    keys.register(registry, keys.WindowsInput(), launched)
    media.register(registry, media.SystemVolume(), media.press_media_key)
    web.register(registry, config.web.search_url, webbrowser.open)
    system.register(registry, system.run_command, datetime.now)
    register_browser(registry)
    register_ui(registry)
    return registry
