from datetime import datetime, timedelta, timezone

import pytest

from jarvis.storage import LocalStore
from jarvis.tools.registry import ToolError
from jarvis.tools.reminder_tools import ReminderService
from jarvis.tools.task_tools import TaskPlanner


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def test_reminder_persists_across_restart_and_fires_once(tmp_path):
    clock = Clock()
    db_path = tmp_path / "jarvis.db"
    service = ReminderService(LocalStore(db_path), clock=clock)
    created = service.add("Submit report", in_minutes=30)
    assert created["scheduled"] and created["id"] == 1
    service.store.close()

    fired = []
    restarted = ReminderService(LocalStore(db_path), notify=fired.append, clock=clock)
    assert [r["message"] for r in restarted.list()["pending"]] == ["Submit report"]
    assert restarted.check_due() == []
    clock.now += timedelta(minutes=31)
    assert len(restarted.check_due()) == 1
    assert restarted.check_due() == []  # never fires twice
    assert fired[0]["message"] == "Submit report" and not fired[0]["overdue"]


def test_overdue_reminder_flagged(tmp_path):
    clock = Clock()
    service = ReminderService(LocalStore(tmp_path / "j.db"), clock=clock)
    service.add("Call mom", in_minutes=1)
    clock.now += timedelta(hours=3)
    assert service.check_due()[0]["overdue"]


def test_reminder_validation(tmp_path):
    service = ReminderService(LocalStore(tmp_path / "j.db"), clock=Clock())
    with pytest.raises(ToolError, match="exactly one"):
        service.add("x")
    with pytest.raises(ToolError, match="exactly one"):
        service.add("x", in_minutes=5, at="2026-10-31 10:00")
    with pytest.raises(ToolError, match="past"):
        service.add("x", at="2020-01-01 10:00")
    with pytest.raises(ToolError, match="2026-10-31 17:30"):
        service.add("x", at="tomorrow evening")
    with pytest.raises(ToolError, match="one year"):
        service.add("x", in_minutes=60 * 24 * 400)
    assert service.add("x", at="2026-10-31T17:30")["scheduled"]


def test_cancel_and_timer(tmp_path):
    service = ReminderService(LocalStore(tmp_path / "j.db"), clock=Clock())
    timer = service.add_timer(5, "Tea")
    assert "Tea finished" in timer["message"]
    assert service.cancel(timer["id"])["cancelled"]
    with pytest.raises(ToolError, match="already cancelled"):
        service.cancel(timer["id"])
    with pytest.raises(ToolError, match="No reminder"):
        service.cancel(99)
    assert service.check_due() == []


def test_task_plan_tracks_real_outcomes(tmp_path):
    planner = TaskPlanner(LocalStore(tmp_path / "j.db"))
    task = planner.create("Prepare demo", ["Open VS Code", "Create notes file", "Set reminder"])
    assert task["status"] == "active" and len(task["steps"]) == 3
    planner.update_step(task["id"], 1, "done")
    planner.update_step(task["id"], 2, "failed", "permission denied")
    task = planner.update_step(task["id"], 3, "done")
    assert task["status"] == "failed"
    assert task["steps"][1]["note"] == "permission denied"
    with pytest.raises(ToolError):
        planner.update_step(task["id"], 9, "done")
    with pytest.raises(ToolError):
        planner.create("Empty", ["  "])
