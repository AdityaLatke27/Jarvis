import time

from jarvis.tools.registry import MAX_RESULT_CHARS, Tool, ToolError, ToolRegistry, validate_args

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 10},
        "count": {"type": "integer", "minimum": 1, "maximum": 5},
        "mode": {"type": "string", "enum": ["a", "b"]},
        "items": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def registry_with(handler, approval=None, timeout=5):
    registry = ToolRegistry()
    registry.register(Tool("demo", "demo tool", SCHEMA, handler, approval=approval, timeout=timeout))
    return registry


def test_validation_errors():
    assert validate_args(SCHEMA, {"name": "ok"}) == []
    assert "missing required argument 'name'" in validate_args(SCHEMA, {})
    assert validate_args(SCHEMA, {"name": "x", "extra": 1}) == ["unknown argument 'extra'"]
    assert validate_args(SCHEMA, {"name": 5})
    assert validate_args(SCHEMA, {"name": "x", "count": True})  # bool is not an integer
    assert validate_args(SCHEMA, {"name": "x", "count": 9})
    assert validate_args(SCHEMA, {"name": "x", "mode": "c"})
    assert validate_args(SCHEMA, {"name": "x", "items": ["a", 2]})
    assert validate_args(SCHEMA, {"name": "x", "items": ["a", "b", "c"]})
    assert validate_args(SCHEMA, {"name": "   "})
    assert validate_args(SCHEMA, []) == ["arguments must be a JSON object"]


def test_unknown_tool_and_malformed_json():
    registry = registry_with(lambda name: name)
    assert registry.execute("nope", "{}").status == "invalid"
    result = registry.execute("demo", "{not json")
    assert not result.ok and result.status == "invalid" and "not valid JSON" in result.content


def test_success_and_handler_failure():
    registry = registry_with(lambda name, **_: {"hello": name})
    result = registry.execute("demo", '{"name": "bob"}')
    assert result.ok and '"hello": "bob"' in result.content

    def failing(name):
        raise ToolError("disk full")
    failed = registry_with(failing).execute("demo", '{"name": "x"}')
    assert not failed.ok and failed.status == "failed" and "disk full" in failed.content


def test_crash_is_contained():
    def crash(name):
        raise ZeroDivisionError("boom")
    result = registry_with(crash).execute("demo", '{"name": "x"}')
    assert not result.ok and "ZeroDivisionError" in result.content


def test_approval_required_denied_and_granted():
    ran = []
    registry = registry_with(lambda name: ran.append(name) or "done", approval=lambda a: f"Do {a['name']}?")
    assert registry.execute("demo", '{"name": "x"}').status == "denied"  # no approver -> deny
    assert registry.execute("demo", '{"name": "x"}', approver=lambda t, p: False).status == "denied"
    assert ran == []
    prompts = []
    result = registry.execute("demo", '{"name": "x"}', approver=lambda t, p: prompts.append(p) or True)
    assert result.ok and ran == ["x"] and prompts == ["Do x?"]


def test_approval_not_needed_when_preview_is_none():
    registry = registry_with(lambda name: "ok", approval=lambda a: None)
    assert registry.execute("demo", '{"name": "x"}').ok


def test_timeout_reports_unknown_outcome():
    registry = registry_with(lambda name: time.sleep(2), timeout=0.2)
    result = registry.execute("demo", '{"name": "x"}')
    assert result.status == "timeout" and "do not claim" in result.content


def test_large_results_truncated():
    result = registry_with(lambda name: "x" * (MAX_RESULT_CHARS + 500)).execute("demo", '{"name": "x"}')
    assert len(result.content) < MAX_RESULT_CHARS + 50
