"""Время, загрузка системы и питание компьютера."""

import subprocess
from collections.abc import Callable
from datetime import datetime

import psutil

from jarvis.tools.registry import Param, Risk, ToolRegistry, ToolResult

_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]  # fmt: skip

# Задержка даёт время отменить командой «shutdown /a», если подтверждение распознали неверно.
_SHUTDOWN_DELAY_SECONDS = 10
_POWER_FLAGS = {"выключить": "/s", "перезагрузить": "/r"}


def register(
    registry: ToolRegistry,
    run: Callable[[list[str]], object],
    now: Callable[[], datetime],
) -> None:
    @registry.tool(description="Узнать текущее время, дату и день недели.")
    def get_datetime() -> ToolResult:
        moment = now()
        return ToolResult(
            ok=True,
            data={
                "time": moment.strftime("%H:%M"),
                "date": f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}",
                "weekday": _WEEKDAYS[moment.weekday()],
            },
        )

    @registry.tool(description="Загрузка процессора, занятая память, место на диске C и заряд батареи.")
    def system_info() -> ToolResult:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        # Целые числа и явное «свободно»: модель путала занятую память со свободной,
        # а «15,9 ГБ» плохо звучит голосом.
        data = {
            "cpu_load_percent": round(psutil.cpu_percent(interval=0.3)),
            "ram_free_gb": round(memory.available / 2**30),
            "ram_used_percent": round(memory.percent),
            "ram_total_gb": round(memory.total / 2**30),
            "disk_c_free_gb": round(disk.free / 2**30),
        }
        battery = psutil.sensors_battery()
        if battery is not None:
            data["battery_percent"] = round(battery.percent)
            data["charging"] = battery.power_plugged
        return ToolResult(ok=True, data=data)

    @registry.tool(
        description="Выключить или перезагрузить компьютер.",
        params={"action": Param("string", "Что сделать", enum=list(_POWER_FLAGS))},
        risk=Risk.CONFIRM,
        confirm="Точно {action} компьютер?",
    )
    def power(action: str) -> ToolResult:
        if action not in _POWER_FLAGS:
            return ToolResult(ok=False, error=f"Неизвестное действие {action}")
        run(["shutdown", _POWER_FLAGS[action], "/t", str(_SHUTDOWN_DELAY_SECONDS)])
        verb = "Выключаю" if action == "выключить" else "Перезагружаю"
        return ToolResult(ok=True, speech=f"{verb} компьютер через {_SHUTDOWN_DELAY_SECONDS} секунд.")


def run_command(command: list[str]) -> None:
    subprocess.run(command, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
