"""Read-only setup check. Run after filling in .env:

    .venv\\Scripts\\python scripts\\check_setup.py

It never writes data, never prints secret values and makes no paid calls:
- OpenRouter: reads key info (GET /api/v1/key) and checks model IDs.
- Supabase:   signs in and reads at most one memory row to confirm columns.
- ElevenLabs: reads account info (GET /v1/user).
- Microphone and offline text-to-speech availability.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from jarvis.config import ConfigError, load_config  # noqa: E402

OK, WARN, FAIL = "  [OK]  ", "  [WARN]", "  [FAIL]"
problems = 0


def report(mark, text):
    global problems
    if mark == FAIL:
        problems += 1
    print(f"{mark} {text}")


def check_openrouter(config):
    print("\nOpenRouter")
    try:
        response = httpx.get("https://openrouter.ai/api/v1/key",
                             headers={"Authorization": f"Bearer {config.openrouter_api_key}"}, timeout=15)
    except httpx.HTTPError as error:
        return report(FAIL, f"Could not reach OpenRouter ({type(error).__name__}).")
    if response.status_code == 401:
        return report(FAIL, "API key rejected. Check OPENROUTER_API_KEY.")
    if response.status_code != 200:
        return report(FAIL, f"Unexpected response {response.status_code}.")
    data = response.json().get("data", {})
    remaining = data.get("limit_remaining")
    report(OK, f"API key valid. Free tier: {data.get('is_free_tier')}. "
               f"Remaining limit: {'unlimited' if remaining is None else remaining}.")
    if remaining is not None and remaining <= 0:
        report(WARN, "The key's credit limit is used up; paid models will fail with 'credits exhausted'.")

    from jarvis.llm import LLMClient
    issues = LLMClient(config).check_models()
    for issue in issues:
        report(FAIL if "not found" in issue or "tool" in issue else WARN, issue)
    if not issues:
        report(OK, f"Model(s) usable with tools: {', '.join([config.model, *config.fallback_models])}")


def check_supabase(config):
    print("\nSupabase memory")
    if not config.memory_configured:
        return report(WARN, "Not configured; JARVIS will run without persistent memory.")
    from jarvis.memory.db import MEMORY_COLUMNS, MemoryDB, MemoryUnavailable, _run
    try:
        db = MemoryDB.connect(config.supabase_url, config.supabase_key)
        user_id = db.sign_in(config.jarvis_email, config.jarvis_password)
        report(OK, "Signed in as the JARVIS user.")
        rows = _run(db.table().select(MEMORY_COLUMNS).eq("user_id", user_id).limit(1), "column check").data
        report(OK, f"memories table readable with all expected columns ({len(rows)} sample row).")
        _run(db.table().select("id").eq("user_id", user_id).wfts("search_vector", "test").limit(1), "search check")
        report(OK, "Full-text search on search_vector works.")
        keys = db.current_keys(user_id)
        report(OK, f"{len(keys)} current fact key(s) found.")
    except MemoryUnavailable as error:
        report(FAIL, str(error))
    except Exception as error:
        report(FAIL, f"{type(error).__name__}: {error}")
    report(WARN, "Not checked (would modify data): set_current_memory RPC and UPDATE/DELETE policies. "
                 "See supabase/inspect_schema.sql.")


def check_elevenlabs(config):
    print("\nElevenLabs voice input")
    if not config.elevenlabs_api_key:
        return report(WARN, "ELEVENLABS_API_KEY is empty; voice input disabled (typing works).")
    try:
        response = httpx.get("https://api.elevenlabs.io/v1/user",
                             headers={"xi-api-key": config.elevenlabs_api_key}, timeout=15)
    except httpx.HTTPError as error:
        return report(FAIL, f"Could not reach ElevenLabs ({type(error).__name__}).")
    if response.status_code == 200:
        sub = response.json().get("subscription", {})
        used, limit = sub.get("character_count"), sub.get("character_limit")
        report(OK, f"API key valid. Plan: {sub.get('tier', '?')}. Credits used: {used} / {limit}.")
    elif response.status_code == 401 and "permission" in response.text.lower():
        report(OK, "API key valid (it has restricted permissions; make sure Speech to Text is enabled).")
    elif response.status_code == 401:
        report(FAIL, "API key rejected. Check ELEVENLABS_API_KEY.")
    else:
        report(WARN, f"Unexpected response {response.status_code}.")


def check_audio(config):
    print("\nAudio devices")
    try:
        import sounddevice as sd
        from jarvis.voice.recorder import parse_device
        device = sd.query_devices(parse_device(config.mic_device), kind="input")
        report(OK, f"Microphone for terminal push-to-talk: {device['name']}")
    except Exception as error:
        report(WARN, f"No microphone found for terminal mode ({type(error).__name__}). The browser HUD uses "
                     "its own microphone access, so it may still work.")
    if config.tts_engine == "pyttsx3":
        try:
            from jarvis.voice.tts import Pyttsx3TTS
            engine = Pyttsx3TTS(config.tts_voice, config.tts_rate, config.data_dir / "tts")
            report(OK, f"Offline voices: {', '.join(engine.voices) or 'none'}")
        except Exception as error:
            report(FAIL, f"Offline TTS unavailable: {error}")
    else:
        report(OK, f"Voice output engine: {config.tts_engine}")


def main():
    try:
        config = load_config()
    except ConfigError as error:
        print(f"[FAIL] Configuration: {error}")
        sys.exit(1)
    print("Configuration loaded.")
    for warning in config.warnings:
        report(WARN, warning)
    check_openrouter(config)
    check_supabase(config)
    check_elevenlabs(config)
    check_audio(config)
    print(f"\n{'All required checks passed.' if not problems else f'{problems} problem(s) found.'}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
