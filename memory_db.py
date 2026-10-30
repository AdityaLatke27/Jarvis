"""Backwards-compatible wrapper around jarvis.memory.db.

JARVIS v2 imported these functions directly. They keep the same names
and arguments, but the Supabase client is now created on first use
instead of at import time, and validation lives in jarvis/.
"""

from jarvis.config import load_config
from jarvis.memory.db import MemoryDB

_db = None


def _client():
    global _db
    if _db is None:
        config = load_config()
        if not config.memory_configured:
            raise RuntimeError("Missing Supabase settings in .env")
        _db = MemoryDB.connect(config.supabase_url, config.supabase_key)
        _db._config = config
    return _db


def sign_in():
    db = _client()
    return db.sign_in(db._config.jarvis_email, db._config.jarvis_password)


def save_memory(user_id, category, content, importance=3):
    category, content = category.strip(), content.strip()
    if not category or not content:
        return False
    if not isinstance(importance, int) or not 1 <= importance <= 5:
        raise ValueError("Importance must be an integer from 1 to 5.")
    _client().insert(user_id, category, content, importance)
    return True


def search_memories(user_id, query, limit=5):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")
    return _client().search(user_id, query, limit, 0, include_history=True)


def set_current_memory(user_id, memory_key, category, content, importance=3):
    result = _client().set_current(memory_key.strip().lower(), category.strip(), content.strip(), importance)
    if not isinstance(result, dict) or not result.get("success"):
        raise RuntimeError(f"Unexpected response from set_current_memory: {result}")
    return result


def get_current_memory(user_id, memory_key):
    return _client().get_current(user_id, memory_key.strip().lower())


def get_memory_history(user_id, memory_key, limit=10):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")
    return _client().get_history(user_id, memory_key.strip().lower(), limit)


def list_saved_memories(user_id, limit=50):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")
    return _client().list_recent(user_id, limit)
