"""Local SQLite storage for reminders and task plans.

This is separate from Supabase long-term memory on purpose: reminders
and task progress are short-lived working state, stored on this PC in
data/jarvis.db so they survive restarts.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    due_at TEXT NOT NULL,            -- UTC ISO-8601
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | fired | cancelled
    fired_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',   -- active | completed | failed | cancelled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_steps (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step INTEGER NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed | skipped
    note TEXT,
    PRIMARY KEY (task_id, step)
);
"""


def utc_now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class LocalStore:
    def __init__(self, path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def query(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        with self.lock:
            cursor = self.db.execute(sql, params)
            self.db.commit()
            return cursor

    def close(self):
        with self.lock:
            self.db.close()

    # ------------------------------------------------------------- reminders
    def add_reminder(self, message, due_at):
        cursor = self.execute(
            "INSERT INTO reminders (message, due_at, created_at) VALUES (?, ?, ?)",
            (message, iso(due_at), iso(utc_now())),
        )
        return cursor.lastrowid

    def pending_reminders(self):
        return self.query("SELECT * FROM reminders WHERE status = 'pending' ORDER BY due_at")

    def due_reminders(self, now=None):
        now = iso(now or utc_now())
        return self.query(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at", (now,)
        )

    def mark_reminder(self, reminder_id, status):
        cursor = self.execute(
            "UPDATE reminders SET status = ?, fired_at = ? WHERE id = ? AND status = 'pending'",
            (status, iso(utc_now()), reminder_id),
        )
        return cursor.rowcount == 1

    def get_reminder(self, reminder_id):
        rows = self.query("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return rows[0] if rows else None

    # ----------------------------------------------------------------- tasks
    def create_task(self, title, steps):
        now = iso(utc_now())
        with self.lock:
            cursor = self.db.execute(
                "INSERT INTO tasks (title, created_at, updated_at) VALUES (?, ?, ?)", (title, now, now)
            )
            task_id = cursor.lastrowid
            self.db.executemany(
                "INSERT INTO task_steps (task_id, step, description) VALUES (?, ?, ?)",
                [(task_id, index, text) for index, text in enumerate(steps, start=1)],
            )
            self.db.commit()
        return task_id

    def get_task(self, task_id):
        tasks = self.query("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not tasks:
            return None
        task = tasks[0]
        task["steps"] = self.query(
            "SELECT step, description, status, note FROM task_steps WHERE task_id = ? ORDER BY step", (task_id,)
        )
        return task

    def list_tasks(self, status=None, limit=10):
        if status:
            rows = self.query("SELECT * FROM tasks WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))
        else:
            rows = self.query("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        return [self.get_task(row["id"]) for row in rows]

    def update_step(self, task_id, step, status, note=None):
        cursor = self.execute(
            "UPDATE task_steps SET status = ?, note = ? WHERE task_id = ? AND step = ?",
            (status, note, task_id, step),
        )
        if cursor.rowcount != 1:
            return False
        self._refresh_task_status(task_id)
        return True

    def set_task_status(self, task_id, status):
        cursor = self.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status, iso(utc_now()), task_id)
        )
        return cursor.rowcount == 1

    def _refresh_task_status(self, task_id):
        steps = self.query("SELECT status FROM task_steps WHERE task_id = ?", (task_id,))
        statuses = {row["status"] for row in steps}
        if "pending" in statuses:
            status = "active"
        elif "failed" in statuses:
            status = "failed"
        else:
            status = "completed"
        self.set_task_status(task_id, status)
