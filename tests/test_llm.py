import logging
from types import SimpleNamespace

import httpx
import openai
import pytest

from jarvis.llm import LLMClient, LLMError, classify_error
from jarvis.logging_setup import RedactSecrets

REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def status_error(cls, code, message="error", headers=None):
    response = httpx.Response(code, request=REQUEST, headers=headers or {})
    return cls(message, response=response, body={"error": {"message": message}})


def ok_response(text="hi"):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], error=None)


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models = []

    def create(self, model, **kwargs):
        self.models.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_client(make_config, outcomes, **env):
    config = make_config(**env)
    completions = FakeCompletions(outcomes)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    sleeps = []
    return LLMClient(config, client=fake, sleep=sleeps.append), completions, sleeps


@pytest.mark.parametrize("error,kind", [
    (status_error(openai.AuthenticationError, 401), "auth"),
    (status_error(openai.APIStatusError, 402, "Insufficient credits"), "credits"),
    (status_error(openai.RateLimitError, 429), "rate_limit"),
    (status_error(openai.NotFoundError, 404, "No endpoints found for x/y"), "model"),
    (status_error(openai.BadRequestError, 400, "x/y is not a valid model ID"), "model"),
    (status_error(openai.BadRequestError, 400, "messages: bad"), "bad_request"),
    (status_error(openai.InternalServerError, 502), "provider"),
    (openai.APITimeoutError(request=REQUEST), "timeout"),
    (openai.APIConnectionError(request=REQUEST), "network"),
])
def test_classify_error(error, kind):
    assert classify_error(error, "x/y").kind == kind


def test_network_error_retried_then_succeeds(make_config):
    client, completions, sleeps = make_client(
        make_config, [openai.APIConnectionError(request=REQUEST), ok_response("done")])
    assert client.chat([{"role": "user", "content": "x"}]).content == "done"
    assert len(sleeps) == 1


def test_retries_are_limited(make_config):
    errors = [openai.APITimeoutError(request=REQUEST)] * 3
    client, completions, sleeps = make_client(make_config, errors, OPENROUTER_MAX_RETRIES=2)
    with pytest.raises(LLMError) as raised:
        client.chat([])
    assert raised.value.kind == "timeout"
    assert len(completions.models) == 3 and len(sleeps) == 2


def test_rate_limit_short_retry_after_waits_once(make_config):
    limited = status_error(openai.RateLimitError, 429, headers={"retry-after": "3"})
    client, completions, sleeps = make_client(make_config, [limited, ok_response()])
    client.chat([])
    assert sleeps == [3.0]


def test_rate_limit_not_blindly_retried_or_fallen_back(make_config):
    limited = status_error(openai.RateLimitError, 429)  # no retry-after
    client, completions, sleeps = make_client(
        make_config, [limited, ok_response()], OPENROUTER_FALLBACK_MODELS="other/model")
    with pytest.raises(LLMError) as raised:
        client.chat([])
    assert raised.value.kind == "rate_limit"
    assert completions.models == ["openrouter/auto"] and sleeps == []


def test_rate_limit_long_wait_is_reported_not_waited(make_config):
    limited = status_error(openai.RateLimitError, 429, headers={"retry-after": "120"})
    client, completions, sleeps = make_client(make_config, [limited])
    with pytest.raises(LLMError):
        client.chat([])
    assert sleeps == []


def test_credits_error_does_not_fall_back(make_config):
    client, completions, _ = make_client(
        make_config, [status_error(openai.APIStatusError, 402)], OPENROUTER_FALLBACK_MODELS="other/model")
    with pytest.raises(LLMError) as raised:
        client.chat([])
    assert raised.value.kind == "credits"
    assert completions.models == ["openrouter/auto"]


def test_invalid_model_falls_back_to_configured_model(make_config):
    client, completions, _ = make_client(
        make_config, [status_error(openai.NotFoundError, 404, "No endpoints found"), ok_response("ok")],
        OPENROUTER_MODEL="bad/model", OPENROUTER_FALLBACK_MODELS="good/model")
    assert client.chat([]).content == "ok"
    assert completions.models == ["bad/model", "good/model"]
    assert client.active_model == "good/model"


def test_http_200_with_error_body_is_provider_error(make_config):
    empty = SimpleNamespace(choices=None, error={"message": "upstream failed"})
    client, _, _ = make_client(make_config, [empty], OPENROUTER_MAX_RETRIES=0)
    with pytest.raises(LLMError) as raised:
        client.chat([])
    assert raised.value.kind == "provider"


def test_log_redaction():
    redactor = RedactSecrets(["my-super-secret"])
    record = logging.LogRecord("x", logging.INFO, __file__, 1,
                               "key=%s token=sk-or-v1-abcdefghijklmnop", ("my-super-secret",), None)
    redactor.filter(record)
    assert "my-super-secret" not in record.msg and "sk-or-v1-abc" not in record.msg
