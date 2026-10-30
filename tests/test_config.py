import base64
import json

import pytest

from jarvis.config import ConfigError, load_config, supabase_key_role


def jwt(role):
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.signature"


def test_missing_openrouter_key_is_clear_error():
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        load_config(env={})


def test_defaults(make_config):
    config = make_config()
    assert config.model == "openrouter/auto"
    assert config.max_history_turns == 12
    assert not config.memory_configured
    assert any("Supabase is not configured" in w for w in config.warnings)


def test_service_role_key_is_rejected(make_config):
    with pytest.raises(ConfigError, match="service_role"):
        make_config(SUPABASE_URL="https://x.supabase.co", SUPABASE_KEY=jwt("service_role"))
    with pytest.raises(ConfigError, match="service_role"):
        make_config(SUPABASE_KEY="sb_secret_abcdefghijklmnop")


def test_anon_key_accepted(make_config):
    config = make_config(SUPABASE_URL="https://x.supabase.co", SUPABASE_KEY=jwt("anon"),
                         JARVIS_EMAIL="a@b.c", JARVIS_PASSWORD="pw123456")
    assert config.memory_configured
    assert supabase_key_role(jwt("anon")) == "anon"


def test_fallback_models_parsed_and_primary_excluded(make_config):
    config = make_config(OPENROUTER_MODEL="openai/gpt-4o-mini",
                         OPENROUTER_FALLBACK_MODELS="openai/gpt-4o-mini, anthropic/claude-haiku-4.5")
    assert config.fallback_models == ("anthropic/claude-haiku-4.5",)


@pytest.mark.parametrize("name,value", [
    ("JARVIS_MAX_HISTORY_TURNS", "abc"),
    ("JARVIS_MAX_HISTORY_TURNS", "0"),
    ("OPENROUTER_MODEL", "not a model"),
    ("JARVIS_TTS_ENGINE", "robot"),
    ("SUPABASE_URL", "http://insecure.example"),
])
def test_invalid_values(make_config, name, value):
    with pytest.raises(ConfigError):
        make_config(**{name: value})


def test_embedding_model_warning(make_config):
    config = make_config(JARVIS_EMBEDDING_MODEL="openai/text-embedding-3-small")
    assert any("EMBEDDING" in w for w in config.warnings)


def test_elevenlabs_tts_without_key_falls_back(make_config):
    config = make_config(JARVIS_TTS_ENGINE="elevenlabs")
    assert config.tts_engine == "pyttsx3"
    assert not config.voice_input_configured


def test_secret_values_listed_for_redaction(make_config):
    config = make_config(ELEVENLABS_API_KEY="el-secret-123456")
    assert "el-secret-123456" in config.secret_values()
