# Changelog

## 3.2.0 — 2026-10-29
- ElevenLabs text-to-speech is the default voice: model `eleven_v4_turbo` (expressive and real-time), voice
  Daniel, with tunable stability, style and speed. If ElevenLabs fails (quota, network), JARVIS
  automatically speaks with the offline Windows voice instead. If a model rejects the voice settings, they
  are retried once with the voice's defaults.

## 3.1.0 — 2026-10-29
- Spoken startup greeting in the HUD (after the boot click) and the terminal. It depends on the time of
  day, uses your saved name if a `preferred_name`/`user_name`/`name` fact exists, and mentions pending
  reminders and offline services. It is built locally, so it makes no AI call and costs no credits.

## 3.0.0 — 2026-10-29

### Phase 1: stability
- Moved from a single `main.py` to the `jarvis/` package. `main.py` is now a small entry point, and
  `memory_db.py` keeps the v2 function names as a compatibility wrapper.
- Centralized, validated configuration (`.env.example` lists every setting). `service_role` Supabase keys are refused.
- OpenRouter errors are explained clearly (bad key, no credits, rate limit, invalid model, provider
  down, network, timeout). Retries are limited, and fallback goes only to configured compatible models.
- Bounded, turn-based conversation history with valid tool-call sequencing.
- Tool registry with argument validation, timeouts, result caps and user approvals.
- Logging to `logs/jarvis.log` with secret redaction.
- Terminal commands: `help`, `status`, `clear`, `memories`, `reminders`, `tasks`, `v`, `stop`, `exit`.

### Phase 2: memory
- Duplicate detection, no history entry for unchanged facts, a similar-key confirmation for ambiguous updates.
- New `correct_memory` and `delete_memory` tools (one record, owner only, user approval required).
- Paged search and inspection. Historical values are excluded unless relevant.
- JARVIS keeps running, with a clear message, when Supabase is unavailable.
- Migration adding owner-only UPDATE/DELETE policies (optional and idempotent).

### Phase 3: voice
- Push-to-talk in the terminal (`v`) and the HUD (hold Space). Speech-to-text via ElevenLabs Scribe.
- Voice output: offline pyttsx3 (default) or ElevenLabs, with a configurable voice and rate. Can be interrupted.

### Phase 4: PC tools
- System info, allowlisted apps, websites and web search, sandboxed files (read, create, approved overwrite and
  delete), document search with PDF and .docx text extraction, persistent reminders and timers, and clipboard.

### Phase 5: planning
- Task plans stored in SQLite with per-step real outcomes. Every reply shows the actual tool results.

### Phase 6: hardware (prepared only)
- Isolated `HardwareInterface`, `NullHardware` and a JSON-lines protocol. No device control.

### HUD
- PIXY's holographic, mic-reactive HUD is now the main screen (`main.py --hud`). File and demo sources
  were removed. Added the backend link, transcript, typed input, push-to-talk, the approval dialog, and
  JARVIS's voice driving the visuals.

### Tests
- 127 pytest tests with mocked OpenRouter, Supabase and ElevenLabs.
