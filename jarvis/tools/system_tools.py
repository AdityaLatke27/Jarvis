"""Read-only tools: date/time and system information."""

import platform
import time
from datetime import datetime

from .registry import Tool

EMPTY = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def get_current_datetime():
    return datetime.now().astimezone().strftime("%A, %d %B %Y, %I:%M:%S %p %Z")


def _gb(value):
    return round(value / 1024 ** 3, 1)


def get_system_info():
    import psutil

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
    info = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.node(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_cores": psutil.cpu_count(logical=True),
        "cpu_usage_percent": psutil.cpu_percent(interval=0.5),
        "memory_used_gb": _gb(memory.used),
        "memory_total_gb": _gb(memory.total),
        "memory_percent": memory.percent,
        "disk_free_gb": _gb(disk.free),
        "disk_total_gb": _gb(disk.total),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "python": platform.python_version(),
    }
    battery = psutil.sensors_battery()
    if battery is not None:
        info["battery_percent"] = round(battery.percent)
        info["plugged_in"] = battery.power_plugged
    return info


def build_system_tools():
    return [
        Tool("get_current_datetime", "Get the current local date and time.", EMPTY, get_current_datetime),
        Tool("get_system_info",
             "Get this PC's OS, CPU usage, memory, disk space, battery and uptime (read-only).",
             EMPTY, get_system_info, timeout=10),
    ]
