# JARVIS

A personal AI assistant for Windows with persistent memory, voice, safe PC tools,
and a holographic, microphone-reactive main screen.

- **Brain:** any tool-calling chat model on [OpenRouter](https://openrouter.ai), via the OpenAI Python SDK
- **Memory:** Supabase (PostgreSQL) with current vs. historical facts that survive restarts
- **Voice:** push-to-talk; speech-to-text by **ElevenLabs Scribe**; replies spoken offline (Windows voices) or by ElevenLabs
- **Main screen:** a three.js HUD (from the PIXY project). The particle core reacts to your
  microphone, and to JARVIS's own voice while it speaks
- **Tools:** reminders/timers, files and documents in allowed folders, allowlisted apps and websites,
  system info, clipboard, multi-step task plans. Risky actions always ask you first

## Quick start

1. **Install** Python 3.12+ and Node.js 18+.
2. **Fill in `.env`.** It was created from `.env.example`. The required values are:

   | Variable | Where to get it |
   | --- | --- |
   | `OPENROUTER_API_KEY` | https://openrouter.ai/settings/keys |
   | `SUPABASE_URL`, `SUPABASE_KEY` | Supabase → Project Settings → API. Use the **anon/publishable** key, never `service_role` |
   | `JARVIS_EMAIL`, `JARVIS_PASSWORD` | The Supabase Auth user JARVIS signs in as |
   | `ELEVENLABS_API_KEY` | https://elevenlabs.io/app/settings/api-keys (enable *Speech to Text*) |

   `OPENROUTER_MODEL` must support tool calling. `openrouter/auto` is the default.
3. **Check the setup.** This is read-only and makes no paid calls:
   ```
   .venv\Scripts\python scripts\check_setup.py
   ```
4. **Run:**
   - `start_hud.bat` runs the holographic main screen in your browser. It builds the HUD on first run.
   - `start_terminal.bat` runs the terminal version.

   Or by hand:
   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   cd hud && npm install && npm run build && cd ..
   .venv\Scripts\python main.py --hud      # or: main.py   (terminal)
   ```

### Optional database migration

`correct_memory` and `delete_memory` need UPDATE/DELETE row-level-security policies on
`public.memories`. First run `supabase/inspect_schema.sql` (read-only) in the Supabase SQL
editor. If no such policies exist, run
`supabase/migrations/20261029120000_owner_update_delete_policies.sql`. It only adds
owner-only policies and never weakens RLS. Without it, those two tools report that the
database refused the change. Nothing else is affected.

## Using the HUD

| Input | Effect |
| --- | --- |
| Hold **Space** or **HOLD TO TALK** | Records while held, then sends the clip to JARVIS |
| Type in the bar, then **Enter** | Sends a typed message (press `/` or `T` to focus the bar) |
| **Esc** / **STOP** | Stops JARVIS speaking |
| **Y** / **N** | Approves or denies an action in the approval dialog |
| Drag / scroll / click | Rotates / zooms / pulses the hologram |
| **H** / **F** / `[` `]` | Hides the HUD / fullscreen / mic gain |

The microphone is analyzed locally for the visuals. Audio leaves your PC only while you hold talk.
The server listens on `127.0.0.1` only and needs a per-launch token, so other websites can't control it.

## Terminal commands

`help`, `status`, `clear` (forgets the conversation, keeps memories), `memories [page]`,
`reminders`, `tasks`, `v` (push-to-talk, press Enter to stop), `stop`, `exit`.

## Safety model

- The model never runs shell commands. Apps and websites come only from `config/apps.json`.
- File tools only work inside `JARVIS_ALLOWED_DIRS` (default: `workspace/`). Paths with `..`,
  other drives, symlinks or junctions that leave those folders are refused.
- Overwriting or deleting a file, deleting or correcting a memory, and reading the clipboard
  all need **your** approval in the terminal or HUD. The model cannot approve its own actions.
- Every reply lists the actual tool outcomes, for example `Actions: open_app [OK] | write_file [DENIED]`.
- Secrets are never saved as memories and are redacted from `logs/jarvis.log`.

## Costs and online services

| Service | Used for | Cost |
| --- | --- | --- |
| OpenRouter | every chat reply | per your model's pricing (free models exist, with rate limits) |
| ElevenLabs | speech-to-text (and TTS if `JARVIS_TTS_ENGINE=elevenlabs`) | uses ElevenLabs credits |
| Supabase | memory | free tier is enough |
| pyttsx3 | default voice output | free and **offline** |

Speech-to-text is **not** offline. It sends your recorded clip to ElevenLabs.

## Tests

```
.venv\Scripts\python -m pytest
```

All external services are mocked. Tests never call paid APIs or touch your Supabase data.

## Project layout

```
main.py               entry point (terminal, or --hud)
memory_db.py          v2-compatible wrapper around jarvis/memory/db.py
jarvis/               the assistant package (see docs/ARCHITECTURE.md)
hud/                  holographic main screen (three.js + Vite)
config/apps.json      allowlisted apps, websites and search engines
supabase/             schema inspection query and migrations
scripts/check_setup.py  read-only setup verification
tests/                pytest suite (mocked services)
docs/                 ARCHITECTURE.md, HARDWARE.md
```

See [CHANGELOG.md](CHANGELOG.md) for what changed from v2.
