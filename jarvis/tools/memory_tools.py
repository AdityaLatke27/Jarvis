"""Memory tools exposed to the model (names kept from JARVIS v2)."""

from .registry import Tool

IMPORTANCE = {"type": "integer", "minimum": 1, "maximum": 5,
              "description": "1 = trivial, 5 = very important"}
KEY = {"type": "string", "maxLength": 100,
       "description": "Stable lowercase key such as main_project"}
MEMORY_ID = {"type": "string", "minLength": 1, "maxLength": 64, "description": "The memory's id"}


def _object(properties, required):
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def build_memory_tools(memory):
    return [
        Tool(
            name="remember_information",
            description=("Save an additional long-term fact when the user asks you to remember something "
                         "that does not replace a current fact. Duplicates are detected automatically."),
            parameters=_object({
                "category": {"type": "string", "minLength": 1, "maxLength": 50},
                "content": {"type": "string", "minLength": 1, "maxLength": 2000},
                "importance": IMPORTANCE,
            }, ["category", "content", "importance"]),
            handler=memory.remember,
        ),
        Tool(
            name="set_current_fact",
            description=(
                "Set or update a current fact while preserving its previous value as history. Use the exact "
                "existing key supplied by the user when provided. content must contain ONLY the fact value, "
                "never the command or explanation: for 'Set key x to Project D. This replaces Project B', "
                "store only 'Project D'. If the result says needs_confirmation, ask the user before retrying. "
                "The tool verifies the stored value before confirming success."
            ),
            parameters=_object({
                "memory_key": KEY,
                "category": {"type": "string", "minLength": 1, "maxLength": 50},
                "content": {"type": "string", "minLength": 1, "maxLength": 2000,
                            "description": "ONLY the fact value to store."},
                "importance": IMPORTANCE,
                "confirm_new_key": {"type": "boolean",
                                    "description": "true only after the user confirmed creating a new key"},
            }, ["memory_key", "category", "content", "importance"]),
            handler=memory.set_current,
        ),
        Tool(
            name="get_current_fact",
            description="Retrieve the current value of a known fact key.",
            parameters=_object({"memory_key": KEY}, ["memory_key"]),
            handler=memory.get_current,
        ),
        Tool(
            name="get_fact_history",
            description="Retrieve previous (historical) values of a fact key, newest first.",
            parameters=_object({
                "memory_key": KEY,
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            }, ["memory_key"]),
            handler=memory.history,
        ),
        Tool(
            name="recall_memories",
            description=("Search saved memories by keywords. Historical values are excluded unless "
                         "include_history is true. Use offset to page through more results."),
            parameters=_object({
                "query": {"type": "string", "minLength": 1, "maxLength": 300},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "offset": {"type": "integer", "minimum": 0, "maximum": 1000},
                "include_history": {"type": "boolean"},
            }, ["query"]),
            handler=memory.search,
        ),
        Tool(
            name="inspect_memories",
            description="List recently saved memories (read-only), newest first, with paging via offset.",
            parameters=_object({
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
            }, []),
            handler=memory.inspect,
        ),
        Tool(
            name="correct_memory",
            description=("Correct the content of one ordinary (non-keyed) memory by id. For keyed facts use "
                         "set_current_fact instead. The user must approve the change."),
            parameters=_object({
                "memory_id": MEMORY_ID,
                "new_content": {"type": "string", "minLength": 1, "maxLength": 2000},
                "importance": IMPORTANCE,
            }, ["memory_id", "new_content"]),
            handler=memory.correct,
            approval=lambda a: (f"Correct this memory?\n  {memory.describe(a['memory_id'])}\n"
                                f"  New content: {a['new_content']}"),
        ),
        Tool(
            name="delete_memory",
            description=("Delete ONE memory record by id, only when the user explicitly asks. Look up the id "
                         "first with recall_memories or inspect_memories. The user must approve."),
            parameters=_object({"memory_id": MEMORY_ID}, ["memory_id"]),
            handler=memory.delete,
            approval=lambda a: f"Permanently delete this memory?\n  {memory.describe(a['memory_id'])}",
        ),
    ]
