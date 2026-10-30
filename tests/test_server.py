import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.assistant import Assistant
from jarvis.conversation import Conversation
from jarvis.memory import MemoryManager
from jarvis.server import create_server
from jarvis.storage import LocalStore
from jarvis.tools.registry import Tool, ToolRegistry

from conftest import ScriptedLLM, reply, tool_call

ORIGIN = {"origin": "http://127.0.0.1:8765"}


class FakeTTS:
    name = "fake"

    def synthesize(self, text):
        return b"RIFF" + text.encode()


class FakeSTT:
    def transcribe(self, audio, filename, mime):
        return "open notepad"


def make_jarvis(make_config, tmp_path, *messages):
    registry = ToolRegistry()
    done = []
    registry.register(Tool("danger", "needs approval", {"type": "object", "properties": {}},
                           lambda: done.append(1) or "did it", approval=lambda a: "Really do it?"))
    llm = ScriptedLLM(*messages)
    jarvis = SimpleNamespace(
        config=make_config(),
        llm=llm,
        memory=MemoryManager(offline_reason="test"),
        stt=FakeSTT(),
        tts=FakeTTS(),
        registry=registry,
        store=LocalStore(tmp_path / "j.db"),
        startup_notes=["test note"],
        assistant=Assistant(llm, registry, Conversation("sys")),
    )
    return jarvis, done


def receive_until(socket, kind):
    for _ in range(20):
        message = json.loads(socket.receive_text())
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} message")


def test_index_injects_token(make_config, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head></head><body></body></html>")
    jarvis, _ = make_jarvis(make_config, tmp_path)
    client = TestClient(create_server(jarvis, token="tok123", hud_dir=dist))
    assert 'content="tok123"' in client.get("/").text


def test_index_explains_missing_build(make_config, tmp_path):
    jarvis, _ = make_jarvis(make_config, tmp_path)
    client = TestClient(create_server(jarvis, token="t", hud_dir=tmp_path / "missing"))
    assert "not built" in client.get("/").text


@pytest.mark.parametrize("url,headers", [
    ("/ws?token=wrong", ORIGIN),
    ("/ws", ORIGIN),
    ("/ws?token=tok", {"origin": "https://evil.example"}),
])
def test_websocket_rejects_untrusted(make_config, tmp_path, url, headers):
    jarvis, _ = make_jarvis(make_config, tmp_path)
    client = TestClient(create_server(jarvis, token="tok", hud_dir=tmp_path))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url, headers=headers) as socket:
            socket.receive_text()


def test_text_round_trip_with_speech(make_config, tmp_path):
    jarvis, _ = make_jarvis(make_config, tmp_path, reply("Hello sir."))
    client = TestClient(create_server(jarvis, token="tok", hud_dir=tmp_path))
    with client.websocket_connect("/ws?token=tok", headers=ORIGIN) as socket:
        hello = receive_until(socket, "hello")
        assert hello["status"]["notes"] == ["test note"] and hello["status"]["memory"] is False
        socket.send_text(json.dumps({"type": "text", "text": "hi"}))
        answer = receive_until(socket, "reply")
        assert answer["text"] == "Hello sir." and answer["ok"]
        speak = receive_until(socket, "speak")
    audio = client.get(speak["url"])
    assert audio.status_code == 200 and audio.content.startswith(b"RIFF")
    assert client.get("/api/tts/unknown.wav").status_code == 404


def test_approval_round_trip(make_config, tmp_path):
    jarvis, done = make_jarvis(make_config, tmp_path,
                               reply(tool_calls=[tool_call("danger")]), reply("Done."))
    client = TestClient(create_server(jarvis, token="tok", hud_dir=tmp_path))
    with client.websocket_connect("/ws?token=tok", headers=ORIGIN) as socket:
        receive_until(socket, "hello")
        socket.send_text(json.dumps({"type": "text", "text": "do the dangerous thing"}))
        request = receive_until(socket, "approval")
        assert request["preview"] == "Really do it?"
        socket.send_text(json.dumps({"type": "approve", "id": request["id"], "approved": True}))
        tool = receive_until(socket, "tool")
        assert tool["ok"] and done == [1]
        assert receive_until(socket, "reply")["summary"] == "Actions: danger [OK]"


def test_voice_clip_is_transcribed(make_config, tmp_path):
    jarvis, _ = make_jarvis(make_config, tmp_path, reply("Opening."))
    client = TestClient(create_server(jarvis, token="tok", hud_dir=tmp_path))
    with client.websocket_connect("/ws?token=tok", headers=ORIGIN) as socket:
        receive_until(socket, "hello")
        socket.send_text(json.dumps({"type": "audio", "mime": "audio/webm;codecs=opus"}))
        socket.send_bytes(b"\x1aE\xdf\xa3" + b"0" * 2000)
        assert receive_until(socket, "transcript")["text"] == "open notepad"
        assert receive_until(socket, "reply")["text"] == "Opening."


def test_audio_without_header_rejected(make_config, tmp_path):
    jarvis, _ = make_jarvis(make_config, tmp_path)
    client = TestClient(create_server(jarvis, token="tok", hud_dir=tmp_path))
    with client.websocket_connect("/ws?token=tok", headers=ORIGIN) as socket:
        receive_until(socket, "hello")
        socket.send_bytes(b"random")
        assert "rejected" in receive_until(socket, "notice")["text"]


def test_clear_keeps_memories(make_config, tmp_path):
    jarvis, _ = make_jarvis(make_config, tmp_path)
    jarvis.assistant.conversation.start_turn("old")
    client = TestClient(create_server(jarvis, token="tok", hud_dir=tmp_path))
    with client.websocket_connect("/ws?token=tok", headers=ORIGIN) as socket:
        receive_until(socket, "hello")
        socket.send_text(json.dumps({"type": "clear"}))
        assert "Memories kept" in receive_until(socket, "notice")["text"]
    assert jarvis.assistant.conversation.turn_count == 0
