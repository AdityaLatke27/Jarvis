
import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
JARVIS_EMAIL = os.getenv("JARVIS_EMAIL")
JARVIS_PASSWORD = os.getenv("JARVIS_PASSWORD")

if not all([SUPABASE_URL, SUPABASE_KEY, JARVIS_EMAIL, JARVIS_PASSWORD]):
    raise RuntimeError("Missing Supabase settings in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def sign_in():
    """Authenticate with Supabase and return the user's ID."""
    response = supabase.auth.sign_in_with_password({
        "email": JARVIS_EMAIL,
        "password": JARVIS_PASSWORD,
    })

    if not response.user:
        raise RuntimeError("Supabase sign-in failed.")

    return response.user.id


def save_memory(user_id, category, content, importance=3):
    """Save an ordinary memory without changing hybrid-memory records."""
    category = category.strip()
    content = content.strip()

    if not category or not content:
        return False

    if not isinstance(importance, int) or not 1 <= importance <= 5:
        raise ValueError("Importance must be an integer from 1 to 5.")

    supabase.table("memories").upsert(
        {
            "user_id": user_id,
            "category": category,
            "content": content,
            "importance": importance,
        },
        on_conflict="user_id,category,content_hash",
        ignore_duplicates=True,
    ).execute()

    return True


def search_memories(user_id, query, limit=5):
    """Search memories using PostgreSQL full-text search."""
    query = query.strip()

    if not query:
        return []

    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")

    result = (
        supabase.table("memories")
        .select(
            "id, category, content, importance, created_at, "
            "memory_key, status, valid_from"
        )
        .eq("user_id", user_id)
        .text_search(
            "search_vector",
            query,
            options={"type": "plain"},
        )
        .execute()
    )

    memories = result.data or []

    memories.sort(
        key=lambda memory: memory.get("importance", 3),
        reverse=True,
    )

    return memories[:limit]


def set_current_memory(
    user_id,
    memory_key,
    category,
    content,
    importance=3,
):
    """
    Atomically update a current memory through the PostgreSQL RPC.
    Previous current values are archived by the database function.
    """
    memory_key = memory_key.strip().lower()
    category = category.strip()
    content = content.strip()

    if not all([memory_key, category, content]):
        raise ValueError(
            "Memory key, category and content are required."
        )

    if not isinstance(importance, int) or not 1 <= importance <= 5:
        raise ValueError("Importance must be an integer from 1 to 5.")

    if len(memory_key) > 100:
        raise ValueError(
            "Memory key must be 100 characters or fewer."
        )

    response = supabase.rpc(
        "set_current_memory",
        {
            "p_memory_key": memory_key,
            "p_category": category,
            "p_content": content,
            "p_importance": importance,
        },
    ).execute()

    result = response.data

    if isinstance(result, list) and result:
        result = result[0]

    if not isinstance(result, dict) or not result.get("success"):
        raise RuntimeError(
            f"Unexpected response from set_current_memory: {result}"
        )

    return result


def get_current_memory(user_id, memory_key):
    """Retrieve the current value for one memory key."""
    memory_key = memory_key.strip().lower()

    result = (
        supabase.table("memories")
        .select(
            "id, category, content, importance, created_at, valid_from"
        )
        .eq("user_id", user_id)
        .eq("memory_key", memory_key)
        .eq("status", "current")
        .execute()
    )

    records = result.data or []

    if not records:
        return None

    return max(
        records,
        key=lambda record: record.get("valid_from") or "",
    )


def get_memory_history(user_id, memory_key, limit=10):
    """Retrieve historical values for one memory key."""
    memory_key = memory_key.strip().lower()

    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")

    result = (
        supabase.table("memories")
        .select(
            "id, category, content, importance, created_at, valid_from"
        )
        .eq("user_id", user_id)
        .eq("memory_key", memory_key)
        .eq("status", "historical")
        .execute()
    )

    records = result.data or []

    records.sort(
        key=lambda record: record.get("valid_from") or "",
        reverse=True,
    )

    return records[:limit]

def list_saved_memories(user_id, limit=50):
    """List recent persistent memories belonging to the signed-in user."""
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")

    result = (
        supabase.table("memories")
        .select(
            "id, category, content, importance, created_at, "
            "memory_key, status, valid_from"
        )
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )

    return result.data or []
