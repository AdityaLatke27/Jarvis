"""Tool registry: one place that describes, validates and runs tools.

Every tool call from the model goes through ToolRegistry.execute(), which:
1. checks the tool exists,
2. parses and validates the JSON arguments against the tool's schema,
3. asks the *user* (never the model) to approve risky actions,
4. runs the handler with a time limit,
5. returns a ToolResult that records what really happened.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 8000
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")


class ToolError(Exception):
    """Raised by a tool handler when the action failed or was refused."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: callable
    # approval(args) -> preview text if the user must approve, else None
    approval: callable = None
    timeout: float = 20.0

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    name: str
    ok: bool
    content: str
    arguments: dict = field(default_factory=dict)
    status: str = "done"  # done | failed | denied | invalid | timeout

    def as_dict(self):
        return {"tool": self.name, "ok": self.ok, "status": self.status}


# ------------------------------------------------------------- validation
_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _check(value, schema, path, errors):
    expected = schema.get("type")
    if expected:
        python_type = _TYPES[expected]
        # bool is a subclass of int; do not accept True as an integer
        if isinstance(value, bool) and expected in {"integer", "number"}:
            errors.append(f"{path} must be {expected}, not boolean")
            return
        if not isinstance(value, python_type):
            errors.append(f"{path} must be {expected}")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} must be <= {schema['maximum']}")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path} is too long (max {schema['maxLength']} characters)")
        if "minLength" in schema and len(value.strip()) < schema["minLength"]:
            errors.append(f"{path} must not be empty")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path} has too many items (max {schema['maxItems']})")
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path} needs at least {schema['minItems']} items")
        for index, item in enumerate(value):
            _check(item, schema.get("items", {}), f"{path}[{index}]", errors)
    if isinstance(value, dict) and expected == "object":
        validate_object(value, schema, errors, prefix=f"{path}.")


def validate_object(args, schema, errors, prefix=""):
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args:
            errors.append(f"missing required argument '{prefix}{name}'")
    for name, value in args.items():
        if name not in properties:
            if schema.get("additionalProperties", True) is False:
                errors.append(f"unknown argument '{prefix}{name}'")
            continue
        _check(value, properties[name], f"{prefix}{name}", errors)


def validate_args(schema, args):
    """Return a list of human-readable problems (empty if valid)."""
    if not isinstance(args, dict):
        return ["arguments must be a JSON object"]
    errors = []
    validate_object(args, schema, errors)
    return errors


# --------------------------------------------------------------- registry
def _to_text(value):
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + " ...[truncated]"
    return text


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        if tool.name in self.tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self.tools[tool.name] = tool

    def register_all(self, tools):
        for tool in tools:
            self.register(tool)

    def schemas(self):
        return [tool.schema() for tool in self.tools.values()]

    def execute(self, name, raw_arguments, approver=None):
        """Run one tool call. `approver(tool_name, preview) -> bool`."""
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(name, False, f"Error: unknown tool '{name}'.", status="invalid")

        if isinstance(raw_arguments, dict):
            args = raw_arguments
        else:
            try:
                args = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError as error:
                return ToolResult(name, False, f"Error: arguments are not valid JSON ({error.msg}).",
                                  status="invalid")

        problems = validate_args(tool.parameters, args)
        if problems:
            return ToolResult(name, False, "Error: invalid arguments: " + "; ".join(problems),
                              args if isinstance(args, dict) else {}, status="invalid")

        if tool.approval is not None:
            try:
                preview = tool.approval(args)
            except ToolError as error:
                return ToolResult(name, False, f"Error: {error}", args, status="failed")
            if preview:
                approved = False
                if approver is not None:
                    try:
                        approved = bool(approver(name, preview))
                    except Exception:
                        log.exception("Approval prompt failed")
                if not approved:
                    log.info("Tool %s denied by user", name)
                    return ToolResult(name, False,
                                      "The user did NOT approve this action. Nothing was changed.",
                                      args, status="denied")

        future = _executor.submit(tool.handler, **args)
        try:
            value = future.result(timeout=tool.timeout)
        except FutureTimeout:
            log.warning("Tool %s timed out after %ss", name, tool.timeout)
            return ToolResult(name, False,
                              f"Error: '{name}' timed out after {tool.timeout:.0f}s. "
                              "Its outcome is unknown; do not claim it succeeded.",
                              args, status="timeout")
        except ToolError as error:
            return ToolResult(name, False, f"Error: {error}", args, status="failed")
        except Exception as error:
            log.exception("Tool %s crashed", name)
            return ToolResult(name, False, f"Tool error: {type(error).__name__}: {error}",
                              args, status="failed")

        log.info("Tool %s succeeded", name)
        return ToolResult(name, True, _to_text(value), args)
