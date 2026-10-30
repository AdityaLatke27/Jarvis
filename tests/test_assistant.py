from jarvis.assistant import Assistant, summarize_outcomes
from jarvis.conversation import Conversation
from jarvis.llm import LLMError
from jarvis.tools.registry import Tool, ToolRegistry

from conftest import ScriptedLLM, reply, tool_call

ECHO_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
               "additionalProperties": False}


def make_assistant(llm, memory=None, rounds=5, approval=None):
    registry = ToolRegistry()
    registry.register(Tool("echo", "echo", ECHO_SCHEMA, lambda text: f"echo:{text}", approval=approval))
    return Assistant(llm, registry, Conversation("system"), memory, rounds)


def test_plain_reply():
    assistant = make_assistant(ScriptedLLM(reply("Hello!")))
    result = assistant.ask("hi")
    assert result.ok and result.reply == "Hello!" and result.outcomes == []


def test_tool_call_sequencing():
    llm = ScriptedLLM(
        reply(tool_calls=[tool_call("echo", '{"text": "a"}', "c1"), tool_call("echo", '{"text": "b"}', "c2")]),
        reply("Both done."),
    )
    assistant = make_assistant(llm)
    result = assistant.ask("do it")
    assert result.reply == "Both done." and [o.ok for o in result.outcomes] == [True, True]
    second_request = llm.calls[1]
    roles = [m["role"] for m in second_request]
    assert roles == ["system", "user", "assistant", "tool", "tool"]
    assert [m["tool_call_id"] for m in second_request if m["role"] == "tool"] == ["c1", "c2"]
    assert second_request[3]["content"] == "echo:a"


def test_malformed_tool_arguments_reported_to_model():
    llm = ScriptedLLM(reply(tool_calls=[tool_call("echo", "{bad", "c1")]), reply("Sorry."))
    result = make_assistant(llm).ask("x")
    assert result.outcomes[0].status == "invalid"
    assert "not valid JSON" in llm.calls[1][-1]["content"]


def test_denied_tool_is_recorded():
    llm = ScriptedLLM(reply(tool_calls=[tool_call("echo", '{"text": "a"}')]), reply("Okay, I won't."))
    result = make_assistant(llm, approval=lambda a: "Echo?").ask("x", approver=lambda t, p: False)
    assert result.outcomes[0].status == "denied"
    assert "DENIED" in summarize_outcomes(result.outcomes)


def test_tool_round_limit():
    calls = [reply(tool_calls=[tool_call("echo", '{"text": "a"}', f"c{i}")]) for i in range(3)]
    result = make_assistant(ScriptedLLM(*calls), rounds=3).ask("loop")
    assert result.error == "tool_limit" and len(result.outcomes) == 3


def test_llm_error_without_actions_rolls_back_turn():
    assistant = make_assistant(ScriptedLLM(LLMError("rate_limit", "Rate limited.")))
    result = assistant.ask("hi")
    assert not result.ok and result.reply == "Rate limited."
    assert assistant.conversation.turn_count == 0


def test_llm_error_after_actions_keeps_valid_history():
    llm = ScriptedLLM(reply(tool_calls=[tool_call("echo", '{"text": "a"}', "c1")]),
                      LLMError("network", "Offline."), reply("Next answer"))
    assistant = make_assistant(llm)
    first = assistant.ask("act")
    assert first.error == "network" and first.outcomes[0].ok
    assistant.ask("next")
    sent = llm.calls[-1]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool", "assistant", "user"]


def test_memory_context_injected(memory):
    memory.remember("projects", "Building JARVIS assistant", 4)
    llm = ScriptedLLM(reply("You are building JARVIS."))
    make_assistant(llm, memory=memory).ask("what am I building with jarvis")
    assert "Building JARVIS assistant" in llm.calls[0][-1]["content"]
