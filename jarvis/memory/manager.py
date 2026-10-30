"""Memory rules on top of MemoryDB: validation, secrets, duplicates,
current-vs-historical facts, and small prompt context.

Every public method returns plain data (dicts/lists) on success and
raises ToolError with a clear message on failure, so the same methods
work for assistant tools, terminal commands and tests.
"""

import difflib
import json
import logging
import re

from ..tools.registry import ToolError
from .db import MemoryUnavailable

log = logging.getLogger(__name__)

KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,99}")
MAX_CONTENT = 2000
CONTEXT_CONTENT_CHARS = 300

SENSITIVE_PATTERNS = [
    re.compile(r"\b(password|passwd|passcode|api[\s_-]?key|secret[\s_-]?key|access[\s_-]?token|"
               r"refresh[\s_-]?token|auth[\s_-]?token|bearer|private[\s_-]?key|otp|pin code)\b", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsb_(secret|publishable)_\w{10,}"),
    re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"),
    re.compile(r"\b(AKIA|ghp_|gho_|xox[bp]-)[A-Za-z0-9]{10,}"),
]

PAST_WORDS = re.compile(
    r"\b(previous|previously|before|used to|history|historical|past|earlier|old|former|formerly|"
    r"changed|last (year|month|week))\b", re.I,
)


def contains_secret(text):
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def _clean_text(value, name, max_len=MAX_CONTENT):
    value = (value or "").strip()
    if not value:
        raise ToolError(f"{name} is required.")
    if len(value) > max_len:
        raise ToolError(f"{name} is too long (max {max_len} characters).")
    return value


def _clean_key(memory_key):
    memory_key = (memory_key or "").strip().lower()
    if not KEY_PATTERN.fullmatch(memory_key):
        raise ToolError("memory_key must be lowercase letters, digits and underscores, starting with a letter.")
    return memory_key


def _check_importance(importance):
    if not isinstance(importance, int) or isinstance(importance, bool) or not 1 <= importance <= 5:
        raise ToolError("importance must be a whole number from 1 to 5.")


def _brief(record):
    """Minimal representation of a memory for prompts and tool results."""
    content = record.get("content") or ""
    if len(content) > CONTEXT_CONTENT_CHARS:
        content = content[:CONTEXT_CONTENT_CHARS] + "..."
    brief = {"id": record.get("id"), "category": record.get("category"), "content": content}
    if record.get("memory_key"):
        brief["key"] = record["memory_key"]
    if record.get("status"):
        brief["status"] = record["status"]
    if record.get("valid_from"):
        brief["since"] = str(record["valid_from"])[:10]
    return brief


class MemoryManager:
    def __init__(self, db=None, user_id=None, offline_reason=None):
        self.db = db
        self.user_id = user_id
        self.offline_reason = offline_reason

    @property
    def available(self):
        return self.db is not None and self.user_id is not None

    def _require(self):
        if not self.available:
            raise ToolError(f"Persistent memory is unavailable: {self.offline_reason or 'not configured'}.")

    def _call(self, method, *args, **kwargs):
        """Call a MemoryDB method by name, turning outages into ToolError."""
        self._require()
        try:
            return getattr(self.db, method)(*args, **kwargs)
        except MemoryUnavailable as error:
            raise ToolError(str(error)) from error

    # ------------------------------------------------------------ ordinary
    def remember(self, category, content, importance=3):
        category = _clean_text(category, "category", 50)
        content = _clean_text(content, "content")
        _check_importance(importance)
        if contains_secret(content) or contains_secret(category):
            raise ToolError("Not saved: the content looks like a password, key or token. Secrets are never stored.")

        existing = self._call("find_exact", self.user_id, category, content)
        if existing:
            return {"saved": False, "duplicate": True, "id": existing.get("id"),
                    "message": "This memory already exists; nothing was added."}

        rows = self._call("insert", self.user_id, category, content, importance)
        if not rows:
            # upsert ignored the row (duplicate by hash) or RLS hid the result
            again = self._call("find_exact", self.user_id, category, content)
            if again:
                return {"saved": False, "duplicate": True, "id": again.get("id"),
                        "message": "This memory already exists; nothing was added."}
            raise ToolError("Supabase did not confirm the save.")
        return {"saved": True, "id": rows[0].get("id"), "message": "Memory saved in Supabase."}

    def search(self, query, limit=5, offset=0, include_history=False):
        query = _clean_text(query, "query", 300)
        rows = self._call("search", self.user_id, query, limit, offset, include_history)
        return {"results": [_brief(r) for r in rows], "offset": offset, "count": len(rows),
                "more_may_exist": len(rows) == limit}

    def inspect(self, limit=20, offset=0):
        rows = self._call("list_recent", self.user_id, limit, offset)
        return {"memories": [_brief(r) for r in rows], "offset": offset, "count": len(rows),
                "more_may_exist": len(rows) == limit}

    # ------------------------------------------------------- current facts
    def get_current(self, memory_key):
        memory_key = _clean_key(memory_key)
        record = self._call("get_current", self.user_id, memory_key)
        if not record:
            return {"found": False, "key": memory_key, "message": "No current value exists for this key."}
        return {"found": True, **_brief(record)}

    def history(self, memory_key, limit=10):
        memory_key = _clean_key(memory_key)
        rows = self._call("get_history", self.user_id, memory_key, limit)
        return {"key": memory_key, "history": [_brief(r) for r in rows], "count": len(rows)}

    def similar_keys(self, memory_key):
        keys = self._call("current_keys", self.user_id)
        return [k for k in difflib.get_close_matches(memory_key, keys, n=3, cutoff=0.75) if k != memory_key]

    def set_current(self, memory_key, category, content, importance=3, confirm_new_key=False):
        memory_key = _clean_key(memory_key)
        category = _clean_text(category, "category", 50)
        content = _clean_text(content, "content")
        _check_importance(importance)
        if contains_secret(content):
            raise ToolError("Update rejected: the content looks like a password, key or token.")

        existing = self._call("get_current", self.user_id, memory_key)
        if existing and existing.get("content") == content:
            return {"success": True, "changed": False, "key": memory_key, "content": content,
                    "message": "The current value is already exactly this; no history entry was created."}

        if not existing and not confirm_new_key:
            similar = self.similar_keys(memory_key)
            if similar:
                return {"success": False, "needs_confirmation": True, "key": memory_key,
                        "similar_existing_keys": similar,
                        "message": "This key does not exist yet but similar keys do. Ask the user which key "
                                   "they mean, then call again with the right key, or with "
                                   "confirm_new_key=true to create a new one."}

        result = self._call("set_current", memory_key, category, content, importance)
        if not isinstance(result, dict) or not result.get("success"):
            raise ToolError(f"Database did not confirm the update: {json.dumps(result, default=str)}")

        saved = self._call("get_current", self.user_id, memory_key)
        if not saved:
            raise ToolError("Update ran, but no current value was found when verifying it.")
        if saved.get("content") != content:
            raise ToolError(f"Verification failed: stored value is '{saved.get('content')}', "
                            f"not the requested value.")
        return {"success": True, "changed": True, "verified": True, "key": memory_key,
                "content": saved["content"], "previous": existing.get("content") if existing else None,
                "message": "Current fact updated and verified; the previous value was kept as history."}

    # ------------------------------------------------- correction/deletion
    def get_record(self, memory_id):
        record = self._call("get_by_id", self.user_id, str(memory_id))
        if not record:
            raise ToolError(f"No memory with id {memory_id} belongs to you.")
        return record

    def correct(self, memory_id, new_content, importance=None):
        new_content = _clean_text(new_content, "new_content")
        if importance is not None:
            _check_importance(importance)
        if contains_secret(new_content):
            raise ToolError("Correction rejected: the content looks like a password, key or token.")
        record = self.get_record(memory_id)
        if record.get("memory_key"):
            raise ToolError("This record is a keyed fact. Use set_current_fact so its history is preserved.")
        if record.get("content") == new_content and importance in (None, record.get("importance")):
            return {"changed": False, "message": "The memory already has this content."}
        rows = self._call("update_content", self.user_id, str(memory_id), new_content, importance)
        if not rows:
            raise ToolError("Supabase did not confirm the correction (it may be blocked by row-level security; "
                            "see supabase/migrations/0002).")
        return {"changed": True, "id": memory_id, "before": record.get("content"), "after": new_content}

    def delete(self, memory_id):
        record = self.get_record(memory_id)
        rows = self._call("delete", self.user_id, str(memory_id))
        if not rows:
            raise ToolError("Supabase did not confirm the deletion (it may be blocked by row-level security; "
                            "see supabase/migrations/0002). Nothing was deleted.")
        return {"deleted": True, "id": memory_id, "content": record.get("content")}

    def describe(self, memory_id):
        """Preview text used when asking the user to approve a change."""
        record = self.get_record(memory_id)
        label = f"key={record['memory_key']}, {record.get('status')}" if record.get("memory_key") else "ordinary"
        return f"Memory #{record.get('id')} [{record.get('category')}, {label}]: {record.get('content')}"

    # ------------------------------------------------------ prompt context
    def context_for(self, user_text, limit=5):
        """Small JSON block of relevant memories for the prompt (or None)."""
        if not self.available:
            return None
        include_history = bool(PAST_WORDS.search(user_text))
        try:
            rows = self.db.search(self.user_id, user_text, limit, 0, include_history)
        except MemoryUnavailable as error:
            log.warning("Memory context skipped: %s", error)
            return None
        if not rows:
            return None
        return json.dumps([_brief(r) for r in rows], ensure_ascii=False, default=str)
