"""Shared test fakes. No test touches real Supabase, OpenRouter or ElevenLabs."""

import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import load_config  # noqa: E402


@pytest.fixture
def make_config(tmp_path):
    def factory(**overrides):
        env = {
            "OPENROUTER_API_KEY": "sk-or-v1-testkeytestkeytestkey",
            "JARVIS_ALLOWED_DIRS": str(tmp_path / "workspace"),
            "JARVIS_DATA_DIR": str(tmp_path / "data"),
        }
        env.update({k: str(v) for k, v in overrides.items()})
        return load_config(env=env)
    return factory


# ------------------------------------------------------------------ LLM fakes
def tool_call(name, arguments="{}", call_id=None):
    return SimpleNamespace(
        id=call_id or f"call_{name}",
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def reply(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or None)


class ScriptedLLM:
    """Returns pre-scripted assistant messages and records what it was sent."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.calls = []
        self.active_model = "test/model"

    def chat(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        item = self.messages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --------------------------------------------------------------- memory fake
class FakeMemoryDB:
    """In-memory stand-in for jarvis.memory.db.MemoryDB."""

    def __init__(self):
        self.rows = []
        self.ids = itertools.count(1)
        self.rpc_calls = 0
        self.fail = None

    def _check(self):
        if self.fail:
            raise self.fail

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _add(self, user_id, category, content, importance, key=None, status=None):
        row = {"id": next(self.ids), "user_id": user_id, "category": category, "content": content,
               "importance": importance, "created_at": self._now(), "memory_key": key,
               "status": status, "valid_from": self._now() if key else None}
        self.rows.append(row)
        return row

    def find_exact(self, user_id, category, content):
        self._check()
        return next((r for r in self.rows if r["user_id"] == user_id and r["category"] == category
                     and r["content"] == content), None)

    def get_by_id(self, user_id, memory_id):
        self._check()
        return next((r for r in self.rows if r["user_id"] == user_id and str(r["id"]) == str(memory_id)), None)

    def search(self, user_id, query, limit=5, offset=0, include_history=False):
        self._check()
        from jarvis.memory.db import search_terms
        terms = search_terms(query).split(" or ")
        hits = [r for r in self.rows if r["user_id"] == user_id
                and any(t and t in r["content"].lower() for t in terms)
                and (include_history or r["status"] != "historical")]
        hits.sort(key=lambda r: -r["importance"])
        return hits[offset: offset + limit]

    def list_recent(self, user_id, limit=50, offset=0):
        self._check()
        rows = [r for r in reversed(self.rows) if r["user_id"] == user_id]
        return rows[offset: offset + limit]

    def get_current(self, user_id, memory_key):
        self._check()
        return next((r for r in self.rows if r["user_id"] == user_id and r["memory_key"] == memory_key
                     and r["status"] == "current"), None)

    def get_history(self, user_id, memory_key, limit=10):
        self._check()
        return [r for r in reversed(self.rows) if r["user_id"] == user_id and r["memory_key"] == memory_key
                and r["status"] == "historical"][:limit]

    def current_keys(self, user_id):
        self._check()
        return sorted({r["memory_key"] for r in self.rows if r["user_id"] == user_id and r["status"] == "current"
                       and r["memory_key"]})

    def insert(self, user_id, category, content, importance):
        self._check()
        if self.find_exact(user_id, category, content):
            return []
        return [self._add(user_id, category, content, importance)]

    def set_current(self, memory_key, category, content, importance):
        self._check()
        self.rpc_calls += 1
        user_id = "user-1"  # the RPC uses auth.uid()
        for row in self.rows:
            if row["user_id"] == user_id and row["memory_key"] == memory_key and row["status"] == "current":
                row["status"] = "historical"
        self._add(user_id, category, content, importance, key=memory_key, status="current")
        return {"success": True}

    def update_content(self, user_id, memory_id, content, importance=None):
        self._check()
        row = self.get_by_id(user_id, memory_id)
        if not row:
            return []
        row["content"] = content
        if importance is not None:
            row["importance"] = importance
        return [row]

    def delete(self, user_id, memory_id):
        self._check()
        row = self.get_by_id(user_id, memory_id)
        if not row:
            return []
        self.rows.remove(row)
        return [row]


@pytest.fixture
def fake_db():
    return FakeMemoryDB()


@pytest.fixture
def memory(fake_db):
    from jarvis.memory.manager import MemoryManager
    return MemoryManager(fake_db, "user-1")
