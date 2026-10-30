"""OpenRouter chat client with clear errors, limited retries and fallback.

Rules:
- Network errors, timeouts and 5xx provider errors are retried a few
  times with exponential backoff.
- Rate limits (429) are retried once, and only when the provider says
  how long to wait and that wait is short. Otherwise the user is told.
- Exhausted credits (402) and bad API keys (401) are never retried.
- Fallback models are used only for model-specific or provider failures.
  Switching models does not reset an account quota, so rate limits and
  credit errors never trigger a fallback.
"""

import logging
import random
import time

import httpx
import openai
from openai import OpenAI

log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(Exception):
    """A chat request failed. `kind` says why; `message` is user-friendly."""

    def __init__(self, kind, message, retry_after=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after

    @property
    def retryable(self):
        return self.kind in {"network", "timeout", "provider"}

    @property
    def allows_fallback(self):
        return self.kind in {"model", "provider"}


def _retry_after_seconds(error):
    response = getattr(error, "response", None)
    if response is None:
        return None
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _error_text(error):
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
    return str(getattr(error, "message", "") or error)


def classify_error(error, model):
    """Turn an SDK exception into an LLMError with a clear message."""
    if isinstance(error, LLMError):
        return error
    text = _error_text(error)
    lowered = text.lower()

    if isinstance(error, openai.APITimeoutError):
        return LLMError("timeout", "The AI request timed out.")
    if isinstance(error, openai.APIConnectionError):
        return LLMError("network", "Could not reach OpenRouter. Check your internet connection.")

    status = getattr(error, "status_code", None)
    if status == 401:
        return LLMError("auth", "OpenRouter rejected the API key. Check OPENROUTER_API_KEY in .env.")
    if status == 402:
        return LLMError("credits", "Your OpenRouter credits are exhausted or too low for this request. "
                                   "Add credits or choose a cheaper/free model.")
    if status == 403:
        return LLMError("forbidden", f"OpenRouter refused the request: {text}")
    if status == 429:
        return LLMError("rate_limit", "The AI provider is rate-limiting requests. Wait a moment and try again.",
                        retry_after=_retry_after_seconds(error))
    model_problem = (
        "not a valid model" in lowered or "no endpoints found" in lowered
        or "model not found" in lowered or "does not support tool" in lowered
    )
    if status == 404 or (status == 400 and model_problem):
        return LLMError("model", f"Model '{model}' is unavailable or cannot use tools: {text}")
    if status == 400:
        return LLMError("bad_request", f"The AI provider rejected the request: {text}")
    if status is not None and status >= 500:
        return LLMError("provider", f"The AI provider had an error ({status}).")
    return LLMError("unknown", f"Unexpected AI error: {type(error).__name__}: {text}")


class LLMClient:
    def __init__(self, config, client=None, sleep=time.sleep):
        self.config = config
        self.models = [config.model, *config.fallback_models]
        self.active_model = config.model
        self.sleep = sleep
        self.client = client or OpenAI(
            api_key=config.openrouter_api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=config.llm_timeout,
            max_retries=0,  # retries are handled here, with the rules above
        )

    def _request(self, model, messages, tools):
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
        )
        # OpenRouter can return HTTP 200 with an error object instead of choices.
        error = getattr(response, "error", None)
        if not getattr(response, "choices", None):
            message = error.get("message") if isinstance(error, dict) else str(error or "empty response")
            raise LLMError("provider", f"The AI provider returned no answer ({message}).")
        return response.choices[0].message

    def _with_retries(self, model, messages, tools):
        attempt = 0
        waited_for_rate_limit = False
        while True:
            try:
                return self._request(model, messages, tools)
            except Exception as raw:
                error = classify_error(raw, model)
                log.warning("LLM request failed (model=%s, kind=%s): %s", model, error.kind, error.message)

                if error.kind == "rate_limit":
                    wait = error.retry_after
                    if (not waited_for_rate_limit and wait is not None
                            and 0 < wait <= self.config.max_rate_limit_wait):
                        waited_for_rate_limit = True
                        log.info("Rate limited; waiting %.1fs as requested by provider", wait)
                        self.sleep(wait)
                        continue
                    raise error

                if error.retryable and attempt < self.config.llm_max_retries:
                    delay = min(8.0, 2 ** attempt) + random.uniform(0, 0.5)
                    attempt += 1
                    self.sleep(delay)
                    continue
                raise error

    def chat(self, messages, tools=None):
        """Send messages and return the assistant message object."""
        last_error = None
        for index, model in enumerate(self.models):
            try:
                message = self._with_retries(model, messages, tools)
                if model != self.active_model:
                    log.info("Using fallback model %s", model)
                self.active_model = model
                return message
            except LLMError as error:
                last_error = error
                has_next = index + 1 < len(self.models)
                if error.allows_fallback and has_next:
                    log.warning("Falling back from %s to %s", model, self.models[index + 1])
                    continue
                raise
        raise last_error

    def check_models(self):
        """Check configured model IDs against OpenRouter's public model list.

        This endpoint is free. Returns a list of warning strings.
        """
        try:
            response = httpx.get(f"{OPENROUTER_BASE_URL}/models", timeout=10)
            response.raise_for_status()
            catalog = {item["id"]: item for item in response.json().get("data", [])}
        except (httpx.HTTPError, ValueError, KeyError) as error:
            return [f"Could not verify model IDs ({type(error).__name__}); continuing."]

        problems = []
        for model in self.models:
            if model == "openrouter/auto":
                continue  # router, not a concrete model
            info = catalog.get(model)
            if info is None:
                problems.append(f"Model '{model}' was not found on OpenRouter.")
            elif "tools" not in (info.get("supported_parameters") or []):
                problems.append(f"Model '{model}' does not support tool calling; JARVIS tools need it.")
        return problems
