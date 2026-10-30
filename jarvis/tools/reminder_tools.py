"""Persistent reminders and timers (stored in data/jarvis.db).

A background thread checks once a second for due reminders and calls
`notify(reminder)`. Reminders that came due while JARVIS was closed are
delivered at the next start, marked as overdue.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from ..storage import utc_now
from .registry import Tool, ToolError

log = logging.getLogger(__name__)
MAX_AHEAD = timedelta(days=366)


def _local(iso_text):
    return datetime.fromisoformat(iso_text).astimezone().strftime("%a %d %b %Y %I:%M %p")


class ReminderService:
    def __init__(self, store, notify=None, clock=utc_now):
        self.store = store
        self.notify = notify or (lambda reminder: None)
        self.clock = clock
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------- tools
    def add(self, message, in_minutes=None, at=None):
        message = message.strip()
        if not message:
            raise ToolError("message is required.")
        if (in_minutes is None) == (at is None):
            raise ToolError("Give exactly one of in_minutes or at.")
        now = self.clock()
        if in_minutes is not None:
            if in_minutes <= 0:
                raise ToolError("in_minutes must be greater than 0.")
            due = now + timedelta(minutes=in_minutes)
        else:
            try:
                due = datetime.fromisoformat(at.strip())
            except ValueError:
                raise ToolError("at must look like 2026-10-31 17:30 (local time).")
            if due.tzinfo is None:
                due = due.astimezone()  # interpret as local time
            if due <= now:
                raise ToolError(f"That time ({_local(due.isoformat())}) is in the past.")
        if due - now > MAX_AHEAD:
            raise ToolError("Reminders can be at most one year ahead.")
        reminder_id = self.store.add_reminder(message, due)
        saved = self.store.get_reminder(reminder_id)
        if not saved:
            raise ToolError("The reminder was not saved.")
        return {"scheduled": True, "id": reminder_id, "message": message, "due_local": _local(saved["due_at"])}

    def add_timer(self, minutes, label="Timer"):
        return self.add(f"{label} finished ({minutes:g} min)", in_minutes=minutes)

    def list(self):
        return {"pending": [{"id": r["id"], "message": r["message"], "due_local": _local(r["due_at"])}
                            for r in self.store.pending_reminders()]}

    def cancel(self, reminder_id):
        reminder = self.store.get_reminder(reminder_id)
        if not reminder:
            raise ToolError(f"No reminder with id {reminder_id}.")
        if reminder["status"] != "pending":
            raise ToolError(f"Reminder {reminder_id} is already {reminder['status']}.")
        if not self.store.mark_reminder(reminder_id, "cancelled"):
            raise ToolError("Could not cancel the reminder.")
        return {"cancelled": True, "id": reminder_id, "message": reminder["message"]}

    # --------------------------------------------------------- scheduler
    def check_due(self):
        """Fire every due reminder once. Returns the reminders fired."""
        now = self.clock()
        fired = []
        for reminder in self.store.due_reminders(now):
            if not self.store.mark_reminder(reminder["id"], "fired"):
                continue  # someone else handled it
            due = datetime.fromisoformat(reminder["due_at"])
            reminder["overdue"] = (now - due) > timedelta(minutes=2)
            fired.append(reminder)
            try:
                self.notify(reminder)
            except Exception:
                log.exception("Reminder notification failed")
        return fired

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="reminders", daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(1.0):
            try:
                self.check_due()
            except Exception:
                log.exception("Reminder check failed")

    def stop(self):
        self._stop.set()


def build_reminder_tools(service):
    return [
        Tool("set_reminder",
             "Schedule a persistent reminder. Use in_minutes for relative times, or at for a local date/time "
             "like '2026-10-31 17:30'. Call get_current_datetime first if you need today's date.",
             {"type": "object", "properties": {
                 "message": {"type": "string", "minLength": 1, "maxLength": 300},
                 "in_minutes": {"type": "number", "minimum": 0.1, "maximum": 527040},
                 "at": {"type": "string", "maxLength": 40}},
              "required": ["message"], "additionalProperties": False},
             service.add),
        Tool("set_timer", "Start a countdown timer that announces when it finishes.",
             {"type": "object", "properties": {
                 "minutes": {"type": "number", "minimum": 0.1, "maximum": 1440},
                 "label": {"type": "string", "maxLength": 60}},
              "required": ["minutes"], "additionalProperties": False},
             service.add_timer),
        Tool("list_reminders", "List pending reminders and timers.",
             {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
             service.list),
        Tool("cancel_reminder", "Cancel a pending reminder or timer by id.",
             {"type": "object", "properties": {"reminder_id": {"type": "integer", "minimum": 1}},
              "required": ["reminder_id"], "additionalProperties": False},
             service.cancel),
    ]
