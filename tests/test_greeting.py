import json
from datetime import datetime

from fastapi.testclient import TestClient

from jarvis.greeting import greeting_text, part_of_day, saved_name
from jarvis.memory import MemoryManager, MemoryUnavailable
from jarvis.server import create_server

from test_server import ORIGIN, make_jarvis, receive_until


def test_part_of_day():
    assert part_of_day(8) == "Good morning"
    assert part_of_day(13) == "Good afternoon"
    assert part_of_day(19) == "Good evening"
    assert part_of_day(2) == "Hello"


def test_greeting_text_mentions_state():
    text = greeting_text(datetime(2026, 10, 29, 9), name="Tony", pending_reminders=2,
                         memory_online=False, voice_input=False)
    assert text.startswith("Good morning, Tony. JARVIS is online and ready.")
    assert "2 pending reminders" in text and "memory is offline" in text and "please type" in text
    plain = greeting_text(datetime(2026, 10, 29, 20))
    assert plain == "Good evening. JARVIS is online and ready. How can I help?"


def test_saved_name_from_memory(memory):
    assert saved_name(memory) is None
    memory.set_current("preferred_name", "personal", "Tony", 4)
    assert saved_name(memory) == "Tony"


def test_saved_name_offline_or_unreachable(memory, fake_db):
    assert saved_name(MemoryManager(offline_reason="x")) is None
    fake_db.fail = MemoryUnavailable("down")
    assert saved_name(memory) is None


def test_hud_greet_is_spoken_without_ai_call(make_config, tmp_path):
    jarvis, _ = make_jarvis(make_config, tmp_path)  # LLM has no scripted replies
    client = TestClient(create_server(jarvis, token="tok", hud_dir=tmp_path))
    with client.websocket_connect("/ws?token=tok", headers=ORIGIN) as socket:
        receive_until(socket, "hello")
        socket.send_text(json.dumps({"type": "greet"}))
        greeting = receive_until(socket, "reply")
        assert greeting["greeting"] and "JARVIS is online" in greeting["text"]
        assert receive_until(socket, "speak")["url"].startswith("/api/tts/")
    assert jarvis.llm.calls == [] and jarvis.assistant.conversation.turn_count == 0
