"""Central configuration: reads .env once and validates every setting.

Other modules receive a Config object instead of calling os.getenv
themselves, so all settings and their checks live in one place.
"""

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Raised when a setting is missing or invalid."""


@dataclass(frozen=True)
class Config:
    # LLM
    openrouter_api_key: str = ""
    model: str = "openrouter/auto"
    fallback_models: tuple = ()
    llm_timeout: float = 60.0
    llm_max_retries: int = 2
    max_rate_limit_wait: float = 10.0
    embedding_model: str = ""

    # Supabase memory
    supabase_url: str = ""
    supabase_key: str = ""
    jarvis_email: str = ""
    jarvis_password: str = ""

    # Conversation
    max_history_turns: int = 12
    max_context_chars: int = 24000
    max_tool_rounds: int = 5

    # Voice
    elevenlabs_api_key: str = ""
    stt_model: str = "scribe_v2"
    stt_language: str = ""
    tts_engine: str = "pyttsx3"
    tts_voice: str = ""
    tts_rate: int = 185
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_tts_model: str = "eleven_v4_turbo"
    elevenlabs_tts_speed: float = 1.0
    elevenlabs_tts_stability: float = 0.45
    elevenlabs_tts_style: float = 0.25
    mic_device: str = ""
    max_record_seconds: float = 15.0

    # PC tools
    allowed_dirs: tuple = ()
    data_dir: Path = PROJECT_ROOT / "data"

    # HUD / logging
    hud_host: str = "127.0.0.1"
    hud_port: int = 8765
    log_level: str = "INFO"

    warnings: list = field(default_factory=list, compare=False)

    # ------------------------------------------------------------ helpers
    @property
    def memory_configured(self):
        return all([self.supabase_url, self.supabase_key,
                    self.jarvis_email, self.jarvis_password])

    @property
    def voice_input_configured(self):
        return bool(self.elevenlabs_api_key)

    def secret_values(self):
        """Values that must never appear in logs or output."""
        return [value for value in (
            self.openrouter_api_key, self.supabase_key,
            self.jarvis_password, self.elevenlabs_api_key,
        ) if value and len(value) >= 6]


# ---------------------------------------------------------------- parsing
def _int(env, name, default, low, high):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got '{raw}'.")
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}.")
    return value


def _float(env, name, default, low, high):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got '{raw}'.")
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}.")
    return value


def supabase_key_role(key):
    """Return the role encoded in a Supabase key, if it can be read.

    Legacy keys are JWTs whose payload contains {"role": "anon"} or
    {"role": "service_role"}. New-style keys start with sb_publishable_
    or sb_secret_.
    """
    if key.startswith("sb_secret_"):
        return "service_role"
    if key.startswith("sb_publishable_"):
        return "anon"
    parts = key.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except (ValueError, json.JSONDecodeError):
        return None


def load_config(env=None, load_env_file=True):
    """Build a validated Config from environment variables.

    Raises ConfigError with a clear message for invalid settings.
    Missing optional services (memory, voice) only add warnings.
    """
    if env is None:
        if load_env_file:
            load_dotenv(PROJECT_ROOT / ".env")
        env = os.environ

    def get(name, default=""):
        return env.get(name, default).strip() or default

    warnings = []

    api_key = get("OPENROUTER_API_KEY")
    if not api_key:
        raise ConfigError(
            "OPENROUTER_API_KEY is missing. Add it to the .env file "
            "(create one at https://openrouter.ai/settings/keys)."
        )

    model = get("OPENROUTER_MODEL", "openrouter/auto")
    if " " in model or "/" not in model:
        raise ConfigError(
            f"OPENROUTER_MODEL '{model}' does not look like an OpenRouter "
            "model ID (expected provider/model, e.g. openai/gpt-4o-mini)."
        )

    fallbacks = tuple(
        item.strip() for item in get("OPENROUTER_FALLBACK_MODELS").split(",")
        if item.strip() and item.strip() != model
    )
    for fallback in fallbacks:
        if " " in fallback or "/" not in fallback:
            raise ConfigError(f"Fallback model '{fallback}' is not a valid model ID.")

    if get("JARVIS_EMBEDDING_MODEL"):
        warnings.append(
            "JARVIS_EMBEDDING_MODEL is set but ignored: memory search uses "
            "PostgreSQL full-text search, and the schema has no vector column."
        )

    supabase_url = get("SUPABASE_URL").rstrip("/")
    supabase_key = get("SUPABASE_KEY")
    if supabase_url and not supabase_url.startswith("https://"):
        raise ConfigError("SUPABASE_URL must start with https://")
    if supabase_key and supabase_key_role(supabase_key) == "service_role":
        raise ConfigError(
            "SUPABASE_KEY is a service_role/secret key. It bypasses row-level "
            "security and must not be used by JARVIS. Use the anon/publishable key."
        )

    memory_values = [supabase_url, supabase_key, get("JARVIS_EMAIL"), get("JARVIS_PASSWORD")]
    if any(memory_values) and not all(memory_values):
        warnings.append(
            "Supabase settings are incomplete (need SUPABASE_URL, SUPABASE_KEY, "
            "JARVIS_EMAIL, JARVIS_PASSWORD). Persistent memory is disabled."
        )
    elif not any(memory_values):
        warnings.append("Supabase is not configured. Persistent memory is disabled.")

    tts_engine = get("JARVIS_TTS_ENGINE", "pyttsx3").lower()
    if tts_engine not in {"pyttsx3", "elevenlabs", "none"}:
        raise ConfigError("JARVIS_TTS_ENGINE must be pyttsx3, elevenlabs or none.")

    elevenlabs_key = get("ELEVENLABS_API_KEY")
    if not elevenlabs_key:
        warnings.append("ELEVENLABS_API_KEY is empty. Voice input is disabled; typing still works.")
        if tts_engine == "elevenlabs":
            warnings.append("ElevenLabs TTS needs ELEVENLABS_API_KEY; falling back to pyttsx3.")
            tts_engine = "pyttsx3"

    allowed = []
    raw_dirs = get("JARVIS_ALLOWED_DIRS")
    for item in raw_dirs.split(";") if raw_dirs else []:
        item = item.strip().strip('"')
        if not item:
            continue
        path = Path(os.path.expandvars(os.path.expanduser(item)))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        allowed.append(path.resolve())
    if not allowed:
        allowed.append((PROJECT_ROOT / "workspace").resolve())

    data_dir = Path(get("JARVIS_DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    hud_host = get("JARVIS_HUD_HOST", "127.0.0.1")
    if hud_host not in {"127.0.0.1", "localhost"}:
        raise ConfigError("JARVIS_HUD_HOST must be 127.0.0.1 or localhost; the HUD can control this PC "
                          "and must not be reachable from the network.")

    log_level = get("JARVIS_LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("JARVIS_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR.")

    return Config(
        openrouter_api_key=api_key,
        model=model,
        fallback_models=fallbacks,
        llm_timeout=_float(env, "OPENROUTER_TIMEOUT_SECONDS", 60.0, 5, 600),
        llm_max_retries=_int(env, "OPENROUTER_MAX_RETRIES", 2, 0, 5),
        max_rate_limit_wait=_float(env, "OPENROUTER_MAX_RATE_LIMIT_WAIT", 10.0, 0, 120),
        embedding_model=get("JARVIS_EMBEDDING_MODEL"),
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        jarvis_email=get("JARVIS_EMAIL"),
        jarvis_password=get("JARVIS_PASSWORD"),
        max_history_turns=_int(env, "JARVIS_MAX_HISTORY_TURNS", 12, 1, 100),
        max_context_chars=_int(env, "JARVIS_MAX_CONTEXT_CHARS", 24000, 2000, 400000),
        max_tool_rounds=_int(env, "JARVIS_MAX_TOOL_ROUNDS", 5, 1, 10),
        elevenlabs_api_key=elevenlabs_key,
        stt_model=get("ELEVENLABS_STT_MODEL", "scribe_v2"),
        stt_language=get("ELEVENLABS_STT_LANGUAGE"),
        tts_engine=tts_engine,
        tts_voice=get("JARVIS_TTS_VOICE"),
        tts_rate=_int(env, "JARVIS_TTS_RATE", 185, 80, 400),
        elevenlabs_voice_id=get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"),
        elevenlabs_tts_model=get("ELEVENLABS_TTS_MODEL", "eleven_v4_turbo"),
        elevenlabs_tts_speed=_float(env, "ELEVENLABS_TTS_SPEED", 1.0, 0.7, 1.2),
        elevenlabs_tts_stability=_float(env, "ELEVENLABS_TTS_STABILITY", 0.45, 0, 1),
        elevenlabs_tts_style=_float(env, "ELEVENLABS_TTS_STYLE", 0.25, 0, 1),
        mic_device=get("JARVIS_MIC_DEVICE"),
        max_record_seconds=_float(env, "JARVIS_MAX_RECORD_SECONDS", 15.0, 2, 120),
        allowed_dirs=tuple(allowed),
        data_dir=data_dir.resolve(),
        hud_host=hud_host,
        hud_port=_int(env, "JARVIS_HUD_PORT", 8765, 1024, 65535),
        log_level=log_level,
        warnings=warnings,
    )
