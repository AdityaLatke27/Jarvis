# Architecture

```
         ┌────────────── interfaces ───────────────┐
         │ cli.py (terminal)    server.py + hud/    │
         └──────────────┬───────────────────────────┘
                        │ ask(text, approver)
                ┌───────▼────────┐
                │  assistant.py  │  model ⇄ tool loop, real outcomes
                └──┬──────────┬──┘
     conversation.py        tools/registry.py ── validate → approve → run (timeout)
     (bounded turns)               │
          │              ┌─────────┼──────────┬──────────┬───────────┐
       llm.py         memory_   app_tools  file_tools  reminder_  task_tools ...
   (OpenRouter,       tools       │            │       tools        │
   retries/fallback)    │      allowlist   allowed dirs  │        storage.py
                   memory/manager.py               storage.py   (SQLite: tasks)
                   memory/db.py ── Supabase       (SQLite: reminders)
```

`app.py` builds all of these from one validated `Config` (`config.py`). Both interfaces
share it.

## Modules

| Module | Responsibility |
| --- | --- |
| `config.py` | Reads `.env` once and validates every value. Rejects `service_role` Supabase keys and non-localhost HUD hosts. |
| `logging_setup.py` | `logs/jarvis.log` (rotating). Known secrets and key-shaped strings are redacted. |
| `llm.py` | OpenRouter client. Turns SDK errors into clear `LLMError` kinds (auth, credits, rate_limit, model, provider, network, timeout). Retries network, timeout and 5xx errors with backoff. Retries a 429 once, and only when the provider gives a short `Retry-After`. Never falls back on 401, 402 or 429. Falls back only to explicitly configured models, and only for model or provider failures. `check_models()` uses the free model list to verify that the IDs exist and support tools. |
| `conversation.py` | History stored as **turns**. Old turns are dropped whole, by count and by character budget, so a `tool_calls` message is never split from its results. `repair_tool_sequence` adds placeholders for missing results and drops orphans. Memory context is attached only to the current request. |
| `assistant.py` | System prompt and the tool loop: at most `JARVIS_MAX_TOOL_ROUNDS` rounds and 6 calls per round. Returns `TurnResult(reply, outcomes)`. On LLM failure it rolls back the turn, unless actions already ran, in which case it records the interruption. |
| `tools/registry.py` | `Tool` definitions (JSON schema, handler, optional `approval(args) -> preview`, timeout). `execute()` parses and validates arguments, asks the **user's** approver, runs the handler in a thread pool with a time limit, and caps result size. |
| `memory/db.py` | The only code that talks to Supabase. Every query filters by `user_id`. Search uses `websearch` full-text with OR'd keywords, ordered by importance, with paging. |
| `memory/manager.py` | Memory rules: secret detection, duplicate prevention, no RPC call when a fact is unchanged, a similar-key check for ambiguous updates, read-back verification, ownership checks before correcting or deleting, and minimal prompt context (current facts only unless the question is about the past). |
| `storage.py` | Local SQLite (`data/jarvis.db`) for reminders and task plans. This is working state, kept separate from long-term memory. |
| `tools/*_tools.py` | Small, separately testable tools: system info, apps and websites, files and documents, reminders and timers, clipboard, task plans. |
| `voice/stt.py` | ElevenLabs Scribe speech-to-text, with error mapping for auth, quota, rate limits, bad audio and network failures. |
| `voice/recorder.py` | Terminal push-to-talk (sounddevice, 16 kHz WAV). Silence is detected locally, so empty clips never cost credits. |
| `voice/tts.py` | pyttsx3 (offline SAPI, on its own COM thread) or ElevenLabs to WAV. `Speaker` plays audio and `stop()` interrupts it. |
| `server.py` | FastAPI and WebSocket backend for the HUD. Listens on localhost, requires a per-launch token and an Origin check. Approvals are relayed to the browser and denied after a timeout or disconnect. |
| `hud/` | three.js hologram from PIXY. `audio.js` analyzes the mic and JARVIS's voice. `link.js` is the WebSocket client. `main.js` handles push-to-talk, typed input and the approval dialog. |
| `hardware/` | Phase 6 placeholder: an abstract interface and a JSON-lines protocol. Not registered as a tool. |

## Request flow (HUD voice)

1. The user holds Space. The browser's `MediaRecorder` records from the same mic stream the visuals use.
2. On release, the client drops clips that are too short or silent. Otherwise it sends `{"type":"audio"}` followed by the binary clip.
3. `Hub.handle_audio` calls ElevenLabs STT, sends a `transcript` event, then runs `assistant.ask`.
4. Tool calls stream to the HUD as `tool` events. Approval requests open a dialog.
5. The `reply` event carries the actual outcome summary. TTS audio is served at `/api/tts/<id>.wav`, and the
   browser plays it through the analyser, so the hologram reacts to JARVIS's voice.

## Database

The repository had no migration history. The existing schema (the `memories` table, the generated
`content_hash` and `search_vector` columns, the unique key `(user_id, category, content_hash)`,
and the `set_current_memory` RPC) is used as it was in v2. It is not recreated here.
`supabase/inspect_schema.sql` dumps the real definitions. The only migration adds owner-only
UPDATE/DELETE policies, if they are missing.

## Decisions

- **Turn-based truncation** instead of message-based, so tool sequences stay valid.
- **The approver belongs to the interface, not the model.** Approval cannot be faked by tool arguments.
- **Fallback is not used for quota errors.** Switching models does not reset an account's limits.
- **SQLite for reminders and tasks.** It works offline and survives restarts without touching user memories.
- **No embeddings.** The schema has no vector column, so `JARVIS_EMBEDDING_MODEL` only produces a warning.
