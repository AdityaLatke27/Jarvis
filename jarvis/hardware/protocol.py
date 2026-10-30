"""Planned Python <-> ESP32 serial protocol: one JSON object per line.

Request : {"v": 1, "id": 7, "cmd": "led.set", "args": {"pin": 2, "on": true}}\n
Response: {"v": 1, "id": 7, "ok": true, "data": {...}} or {"v": 1, "id": 7, "ok": false, "error": "..."}\n

Only commands in ALLOWED_COMMANDS may be sent. Not used by the assistant yet.
"""

import json
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 512
ALLOWED_COMMANDS = {"ping", "status", "led.set", "sensor.read"}


class ProtocolError(ValueError):
    pass


@dataclass
class Command:
    id: int
    cmd: str
    args: dict = field(default_factory=dict)


@dataclass
class Response:
    id: int
    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""


def encode_command(command):
    if command.cmd not in ALLOWED_COMMANDS:
        raise ProtocolError(f"Command '{command.cmd}' is not allowed.")
    line = json.dumps({"v": PROTOCOL_VERSION, "id": command.id, "cmd": command.cmd, "args": command.args},
                      separators=(",", ":")) + "\n"
    data = line.encode("ascii")
    if len(data) > MAX_LINE_BYTES:
        raise ProtocolError("Command is too long.")
    return data


def decode_response(line):
    if len(line) > MAX_LINE_BYTES:
        raise ProtocolError("Response line is too long.")
    try:
        message = json.loads(line.decode("ascii").strip())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError(f"Malformed response: {error}") from error
    if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("Unsupported protocol version.")
    if not isinstance(message.get("id"), int) or not isinstance(message.get("ok"), bool):
        raise ProtocolError("Response needs integer id and boolean ok.")
    return Response(message["id"], message["ok"], message.get("data") or {}, str(message.get("error") or ""))
