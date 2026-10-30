"""Startup greeting. Built locally (no AI call), so it costs no credits."""

from datetime import datetime

from .tools.registry import ToolError

NAME_KEYS = ("preferred_name", "user_name", "name", "my_name")


def part_of_day(hour):
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 22:
        return "Good evening"
    return "Hello"


def saved_name(memory):
    """The user's name if it was saved as a current fact, else None."""
    if memory is None or not memory.available:
        return None
    for key in NAME_KEYS:
        try:
            fact = memory.get_current(key)
        except ToolError:
            return None  # Supabase unreachable: skip quietly
        content = (fact.get("content") or "").strip() if fact.get("found") else ""
        if 0 < len(content) <= 40:
            return content
    return None


def greeting_text(now=None, name=None, pending_reminders=0, memory_online=True, voice_input=True):
    now = now or datetime.now()
    opening = part_of_day(now.hour) + (f", {name}" if name else "") + "."
    parts = [opening, "JARVIS is online and ready."]
    if pending_reminders:
        parts.append(f"You have {pending_reminders} pending reminder{'s' if pending_reminders != 1 else ''}.")
    if not memory_online:
        parts.append("Note: long-term memory is offline.")
    if not voice_input:
        parts.append("Voice input is unavailable, so please type.")
    parts.append("How can I help?")
    return " ".join(parts)


def greet_for(app):
    """Greeting for a running JarvisApp (or anything with the same attributes)."""
    return greeting_text(
        name=saved_name(app.memory),
        pending_reminders=len(app.store.pending_reminders()),
        memory_online=app.memory.available,
        voice_input=app.stt is not None,
    )
