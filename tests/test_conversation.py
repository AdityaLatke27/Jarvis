from jarvis.conversation import INCOMPLETE_TOOL_RESULT, Conversation, repair_tool_sequence


def call(call_id, name="t"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}


def add_tool_turn(conv, text, call_id):
    conv.start_turn(text)
    conv.add({"role": "assistant", "content": "", "tool_calls": [call(call_id)]})
    conv.add({"role": "tool", "tool_call_id": call_id, "content": "result"})
    conv.add({"role": "assistant", "content": "answer"})


def test_turn_limit_drops_whole_turns():
    conv = Conversation("sys", max_turns=2, max_chars=100000)
    for index in range(5):
        add_tool_turn(conv, f"q{index}", f"c{index}")
    messages = conv.build_messages()
    assert messages[0]["role"] == "system"
    assert [m["content"] for m in messages if m["role"] == "user"] == ["q3", "q4"]
    # every tool result still follows its assistant tool call
    assert messages[1]["role"] == "user" and messages[2]["tool_calls"][0]["id"] == "c3"
    assert messages[3]["tool_call_id"] == "c3"


def test_char_limit_keeps_current_turn():
    conv = Conversation("sys", max_turns=50, max_chars=2000)
    for index in range(10):
        conv.start_turn("x" * 500)
        conv.add({"role": "assistant", "content": "y" * 100})
    conv.start_turn("current question")
    messages = conv.build_messages()
    assert messages[-1]["content"] == "current question"
    assert sum(len(m.get("content") or "") for m in messages) <= 2000 + 600


def test_memory_context_only_on_current_message_and_not_stored():
    conv = Conversation("sys")
    conv.start_turn("first")
    conv.add({"role": "assistant", "content": "ok"})
    conv.start_turn("what is my project?")
    messages = conv.build_messages(memory_context='[{"content": "Project D"}]')
    assert "SAVED MEMORIES" in messages[-1]["content"] and "Project D" in messages[-1]["content"]
    assert messages[1]["content"] == "first"
    assert conv.turns[-1][0]["content"] == "what is my project?"


def test_repair_adds_missing_tool_results_and_drops_orphans():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "orphan", "content": "x"},
        {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
    ]
    repaired = repair_tool_sequence(messages)
    assert [m.get("tool_call_id") for m in repaired if m["role"] == "tool"] == ["a", "b"]
    assert repaired[-1]["content"] == INCOMPLETE_TOOL_RESULT


def test_clear_and_rollback():
    conv = Conversation("sys")
    conv.start_turn("a")
    conv.rollback_turn()
    assert conv.turn_count == 0
    conv.start_turn("b")
    conv.clear()
    assert conv.build_messages() == [{"role": "system", "content": "sys"}]


def test_old_long_tool_results_truncated_in_history():
    conv = Conversation("sys", max_chars=100000)
    conv.start_turn("q")
    conv.add({"role": "assistant", "content": "", "tool_calls": [call("a")]})
    conv.add({"role": "tool", "tool_call_id": "a", "content": "z" * 10000})
    conv.add({"role": "assistant", "content": "done"})
    conv.start_turn("next")
    tool_message = [m for m in conv.build_messages() if m["role"] == "tool"][0]
    assert len(tool_message["content"]) < 5000
