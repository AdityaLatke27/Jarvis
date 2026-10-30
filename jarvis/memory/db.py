"""Supabase data access for the `memories` table.

Only this module talks to Supabase. Every query is filtered by user_id,
and row-level security in the database enforces the same rule.
The anon/publishable key is used, never a service_role key.
"""

import logging
import re

import httpx
from postgrest.exceptions import APIError

log = logging.getLogger(__name__)

MEMORY_COLUMNS = "id, category, content, importance, created_at, memory_key, status, valid_from"

STOPWORDS = set("""
a an and are as at be but by can could did do does for from had has have how i if in into is it
its me my of on or our please so tell than that the their them then there these they this to us
was we were what when where which who why will with would you your about remember know
""".split())


class MemoryUnavailable(Exception):
    """Supabase could not be reached or rejected the request."""


def _run(query, action):
    """Execute a query and convert network/database errors to MemoryUnavailable."""
    try:
        return query.execute()
    except APIError as error:
        log.warning("Supabase error during %s: %s", action, error.message)
        raise MemoryUnavailable(f"Database error during {action}: {error.message}") from error
    except (httpx.HTTPError, OSError) as error:
        log.warning("Supabase unreachable during %s: %s", action, type(error).__name__)
        raise MemoryUnavailable(f"Supabase is unreachable ({type(error).__name__}).") from error


def search_terms(text, max_terms=8):
    """Keywords from a sentence, joined for PostgreSQL websearch with OR."""
    words = re.findall(r"[A-Za-z0-9]+", text.casefold())
    keywords = []
    for word in words:
        if len(word) > 1 and word not in STOPWORDS and word not in keywords:
            keywords.append(word)
    return " or ".join(keywords[:max_terms])


class MemoryDB:
    def __init__(self, client):
        self.client = client

    @classmethod
    def connect(cls, url, key):
        from supabase import create_client  # imported lazily for fast tests
        return cls(create_client(url, key))

    def table(self):
        return self.client.table("memories")

    # ------------------------------------------------------------------ auth
    def sign_in(self, email, password):
        try:
            response = self.client.auth.sign_in_with_password({"email": email, "password": password})
        except (httpx.HTTPError, OSError) as error:
            raise MemoryUnavailable(f"Supabase is unreachable ({type(error).__name__}).") from error
        except Exception as error:
            # supabase_auth raises AuthApiError for wrong credentials
            raise MemoryUnavailable(f"Supabase sign-in failed: {getattr(error, 'message', error)}") from error
        if not response.user:
            raise MemoryUnavailable("Supabase sign-in failed.")
        return response.user.id

    # ------------------------------------------------------------------ read
    def find_exact(self, user_id, category, content):
        result = _run(
            self.table().select(MEMORY_COLUMNS)
            .eq("user_id", user_id).eq("category", category).eq("content", content).limit(1),
            "duplicate check",
        )
        return (result.data or [None])[0]

    def get_by_id(self, user_id, memory_id):
        result = _run(
            self.table().select(MEMORY_COLUMNS).eq("user_id", user_id).eq("id", memory_id).limit(1),
            "memory lookup",
        )
        return (result.data or [None])[0]

    def search(self, user_id, query, limit=5, offset=0, include_history=False):
        terms = search_terms(query)
        if not terms:
            return []
        builder = (
            self.table().select(MEMORY_COLUMNS)
            .eq("user_id", user_id)
            .wfts("search_vector", terms)
        )
        if not include_history:
            # Rows without a status are ordinary memories; keep them.
            builder = builder.or_("status.is.null,status.neq.historical")
        builder = (
            builder.order("importance", desc=True)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        return _run(builder, "memory search").data or []

    def list_recent(self, user_id, limit=50, offset=0):
        result = _run(
            self.table().select(MEMORY_COLUMNS)
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1),
            "memory listing",
        )
        return result.data or []

    def get_current(self, user_id, memory_key):
        result = _run(
            self.table().select(MEMORY_COLUMNS)
            .eq("user_id", user_id).eq("memory_key", memory_key).eq("status", "current"),
            "current fact lookup",
        )
        records = result.data or []
        if not records:
            return None
        return max(records, key=lambda record: record.get("valid_from") or "")

    def get_history(self, user_id, memory_key, limit=10):
        result = _run(
            self.table().select(MEMORY_COLUMNS)
            .eq("user_id", user_id).eq("memory_key", memory_key).eq("status", "historical")
            .order("valid_from", desc=True).limit(limit),
            "fact history lookup",
        )
        return result.data or []

    def current_keys(self, user_id):
        result = _run(
            self.table().select("memory_key")
            .eq("user_id", user_id).eq("status", "current").not_.is_("memory_key", "null"),
            "fact key listing",
        )
        return sorted({row["memory_key"] for row in result.data or [] if row.get("memory_key")})

    # ----------------------------------------------------------------- write
    def insert(self, user_id, category, content, importance):
        result = _run(
            self.table().upsert(
                {"user_id": user_id, "category": category, "content": content, "importance": importance},
                on_conflict="user_id,category,content_hash",
                ignore_duplicates=True,
            ),
            "memory save",
        )
        return result.data or []

    def set_current(self, memory_key, category, content, importance):
        """Call the set_current_memory RPC (it archives the previous value)."""
        result = _run(
            self.client.rpc("set_current_memory", {
                "p_memory_key": memory_key,
                "p_category": category,
                "p_content": content,
                "p_importance": importance,
            }),
            "current fact update",
        ).data
        if isinstance(result, list) and result:
            result = result[0]
        return result

    def update_content(self, user_id, memory_id, content, importance=None):
        values = {"content": content}
        if importance is not None:
            values["importance"] = importance
        result = _run(
            self.table().update(values).eq("user_id", user_id).eq("id", memory_id),
            "memory correction",
        )
        return result.data or []

    def delete(self, user_id, memory_id):
        result = _run(
            self.table().delete().eq("user_id", user_id).eq("id", memory_id),
            "memory deletion",
        )
        return result.data or []
