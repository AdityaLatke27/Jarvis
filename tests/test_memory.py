import json

import pytest

from jarvis.memory.db import MemoryUnavailable, search_terms
from jarvis.memory.manager import MemoryManager, contains_secret
from jarvis.tools.memory_tools import build_memory_tools
from jarvis.tools.registry import ToolError, ToolRegistry


def test_remember_and_duplicate_prevention(memory, fake_db):
    first = memory.remember("preferences", "I like dark mode", 3)
    assert first["saved"] is True
    second = memory.remember("preferences", "I like dark mode", 3)
    assert second["duplicate"] is True and second["saved"] is False
    assert len(fake_db.rows) == 1


@pytest.mark.parametrize("content", [
    "my password is hunter2",
    "api key sk-abcdefghijklmnopqrstuvwx",
    "token eyJabcdefghijkl.eyJabcdefghijkl.abcdefghijklmn",
])
def test_secrets_never_saved(memory, fake_db, content):
    with pytest.raises(ToolError, match="never stored|password"):
        memory.remember("notes", content, 3)
    assert fake_db.rows == []


@pytest.mark.parametrize("bad", [0, 6, True, "3"])
def test_invalid_importance(memory, bad):
    with pytest.raises(ToolError):
        memory.remember("notes", "valid", bad)


def test_current_fact_update_preserves_history(memory, fake_db):
    memory.set_current("main_project", "projects", "Project B", 4)
    result = memory.set_current("main_project", "projects", "Project D", 4)
    assert result["verified"] and result["previous"] == "Project B"
    assert memory.get_current("main_project")["content"] == "Project D"
    history = memory.history("main_project")["history"]
    assert [h["content"] for h in history] == ["Project B"]


def test_unchanged_value_creates_no_history(memory, fake_db):
    memory.set_current("city", "personal", "Pune", 3)
    calls = fake_db.rpc_calls
    result = memory.set_current("city", "personal", "Pune", 3)
    assert result["changed"] is False
    assert fake_db.rpc_calls == calls
    assert memory.history("city")["count"] == 0


def test_ambiguous_new_key_needs_confirmation(memory, fake_db):
    memory.set_current("main_project", "projects", "Project B", 4)
    result = memory.set_current("main_projects", "projects", "Project C", 4)
    assert result["needs_confirmation"] and "main_project" in result["similar_existing_keys"]
    assert memory.get_current("main_project")["content"] == "Project B"
    confirmed = memory.set_current("main_projects", "projects", "Project C", 4, confirm_new_key=True)
    assert confirmed["success"]


def test_verification_failure_is_reported(memory, fake_db):
    original = fake_db.get_current

    def tampered(user_id, key):
        row = original(user_id, key)
        return {**row, "content": "tampered"} if row else None

    fake_db.get_current = tampered
    with pytest.raises(ToolError, match="Verification failed"):
        memory.set_current("x_key", "c", "value", 3, confirm_new_key=True)


def test_invalid_key_rejected(memory):
    for key in ["Main Project", "1abc", "", "a" * 101, "drop;table"]:
        with pytest.raises(ToolError):
            memory.get_current(key)


def test_search_excludes_history_unless_requested(memory):
    memory.set_current("laptop", "devices", "Dell laptop", 3)
    memory.set_current("laptop", "devices", "Lenovo laptop", 3)
    current = memory.search("which laptop")["results"]
    assert [r["content"] for r in current] == ["Lenovo laptop"]
    everything = memory.search("which laptop", include_history=True)["results"]
    assert {r["content"] for r in everything} == {"Dell laptop", "Lenovo laptop"}


def test_inspect_pagination(memory):
    for index in range(7):
        memory.remember("notes", f"note number {index}", 2)
    page1 = memory.inspect(limit=5, offset=0)
    page2 = memory.inspect(limit=5, offset=5)
    assert page1["count"] == 5 and page1["more_may_exist"]
    assert page2["count"] == 2 and not page2["more_may_exist"]


def test_delete_one_record_only(memory, fake_db):
    keep = memory.remember("notes", "keep this", 2)["id"]
    remove = memory.remember("notes", "remove this", 2)["id"]
    assert memory.delete(str(remove))["deleted"]
    assert [r["id"] for r in fake_db.rows] == [keep]
    with pytest.raises(ToolError, match="No memory"):
        memory.delete(str(remove))


def test_cannot_touch_other_users_records(memory, fake_db):
    other = fake_db._add("someone-else", "notes", "not yours", 3)
    with pytest.raises(ToolError, match="No memory"):
        memory.delete(str(other["id"]))
    with pytest.raises(ToolError):
        memory.correct(str(other["id"]), "changed")
    assert other in fake_db.rows and other["content"] == "not yours"


def test_correct_ordinary_but_not_keyed(memory):
    note = memory.remember("notes", "meeting on Mondy", 2)["id"]
    assert memory.correct(str(note), "meeting on Monday")["changed"]
    memory.set_current("city", "personal", "Pune", 3)
    keyed = memory.get_current("city")["id"]
    with pytest.raises(ToolError, match="set_current_fact"):
        memory.correct(str(keyed), "Mumbai")


def test_delete_requires_approval_through_registry(memory, fake_db):
    registry = ToolRegistry()
    registry.register_all(build_memory_tools(memory))
    note = memory.remember("notes", "delete me", 2)["id"]
    denied = registry.execute("delete_memory", json.dumps({"memory_id": str(note)}))
    assert denied.status == "denied" and len(fake_db.rows) == 1
    prompts = []
    approved = registry.execute("delete_memory", json.dumps({"memory_id": str(note)}),
                                approver=lambda t, p: prompts.append(p) or True)
    assert approved.ok and fake_db.rows == [] and "delete me" in prompts[0]


def test_unavailable_service_is_graceful(memory, fake_db):
    fake_db.fail = MemoryUnavailable("Supabase is unreachable (ConnectError).")
    with pytest.raises(ToolError, match="unreachable"):
        memory.remember("notes", "x y", 2)
    assert memory.context_for("anything") is None


def test_offline_manager():
    offline = MemoryManager(offline_reason="not configured")
    assert not offline.available
    with pytest.raises(ToolError, match="unavailable"):
        offline.inspect()
    assert offline.context_for("hi") is None


def test_context_is_minimal_and_current_by_default(memory):
    memory.set_current("editor", "tools", "VS Code editor " + "x" * 600, 3)
    memory.set_current("editor", "tools", "Neovim editor", 3)
    context = json.loads(memory.context_for("what editor do I use"))
    assert [c["content"] for c in context] == ["Neovim editor"]
    assert set(context[0]) <= {"id", "category", "content", "key", "status", "since"}
    past = json.loads(memory.context_for("what editor did I use previously"))
    assert any(c["status"] == "historical" and len(c["content"]) <= 303 for c in past)


def test_search_terms():
    assert search_terms("What is my main project?") == "main or project"
    assert search_terms("the and of") == ""
    assert contains_secret("my API key is abc")
    assert not contains_secret("I like keyboards")
