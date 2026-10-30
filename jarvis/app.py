"""Builds the JARVIS application from a Config.

Both interfaces (terminal and HUD) call build_app() so they share the
same assistant, tools, memory, reminders and voice setup.
"""

import logging
from dataclasses import dataclass

from .assistant import SYSTEM_PROMPT, Assistant
from .config import Config
from .conversation import Conversation
from .llm import LLMClient
from .memory import MemoryDB, MemoryManager, MemoryUnavailable
from .storage import LocalStore
from .tools.app_tools import build_app_tools
from .tools.clipboard_tools import Clipboard, build_clipboard_tools
from .tools.file_tools import SafeFiles, build_file_tools
from .tools.memory_tools import build_memory_tools
from .tools.registry import ToolRegistry
from .tools.reminder_tools import ReminderService, build_reminder_tools
from .tools.system_tools import build_system_tools
from .tools.task_tools import TaskPlanner, build_task_tools
from .voice import ElevenLabsSTT, Speaker, VoiceError, make_tts

log = logging.getLogger(__name__)


@dataclass
class JarvisApp:
    config: Config
    assistant: Assistant
    llm: LLMClient
    memory: MemoryManager
    registry: ToolRegistry
    store: LocalStore
    reminders: ReminderService
    files: SafeFiles
    stt: object  # ElevenLabsSTT or None
    tts: object  # TTS engine
    speaker: Speaker
    startup_notes: list

    def shutdown(self):
        self.reminders.stop()
        self.speaker.stop()
        self.store.close()


def connect_memory(config):
    if not config.memory_configured:
        return MemoryManager(offline_reason="Supabase is not configured in .env")
    try:
        db = MemoryDB.connect(config.supabase_url, config.supabase_key)
        user_id = db.sign_in(config.jarvis_email, config.jarvis_password)
        return MemoryManager(db, user_id)
    except MemoryUnavailable as error:
        return MemoryManager(offline_reason=str(error))
    except Exception as error:  # bad URL format etc.
        return MemoryManager(offline_reason=f"{type(error).__name__}: {error}")


def build_app(config, notify=None, check_models=True):
    """Create everything. `notify(reminder)` is called when a reminder fires."""
    notes = list(config.warnings)

    llm = LLMClient(config)
    if check_models:
        notes.extend(llm.check_models())

    memory = connect_memory(config)
    if not memory.available and config.memory_configured:
        notes.append(f"Persistent memory OFFLINE: {memory.offline_reason}")

    store = LocalStore(config.data_dir / "jarvis.db")
    reminders = ReminderService(store, notify=notify)
    files = SafeFiles(config.allowed_dirs)

    registry = ToolRegistry()
    registry.register_all(build_system_tools())
    registry.register_all(build_memory_tools(memory))
    registry.register_all(build_app_tools())
    registry.register_all(build_file_tools(files))
    registry.register_all(build_reminder_tools(reminders))
    registry.register_all(build_task_tools(TaskPlanner(store)))
    try:
        registry.register_all(build_clipboard_tools(Clipboard()))
    except ImportError:
        notes.append("Clipboard tools disabled (pyperclip not installed).")

    stt = None
    if config.voice_input_configured:
        try:
            stt = ElevenLabsSTT(config.elevenlabs_api_key, config.stt_model, config.stt_language)
        except VoiceError as error:
            notes.append(error.message)

    tts = make_tts(config)
    if tts.name != config.tts_engine:
        notes.append(f"Text-to-speech engine '{config.tts_engine}' unavailable; voice output is off.")
    speaker = Speaker(tts, config.data_dir / "tts")

    conversation = Conversation(SYSTEM_PROMPT, config.max_history_turns, config.max_context_chars)
    assistant = Assistant(llm, registry, conversation, memory, config.max_tool_rounds)

    for note in notes:
        log.warning("Startup: %s", note)
    return JarvisApp(config, assistant, llm, memory, registry, store, reminders, files,
                     stt, tts, speaker, notes)


def status_lines(app):
    config = app.config
    memory = "online" if app.memory.available else f"OFFLINE ({app.memory.offline_reason})"
    return [
        f"Model:        {app.llm.active_model}"
        + (f" (fallbacks: {', '.join(config.fallback_models)})" if config.fallback_models else ""),
        f"Memory:       {memory}",
        f"Voice input:  {'ElevenLabs ' + config.stt_model if app.stt else 'disabled (no ELEVENLABS_API_KEY)'}",
        f"Voice output: {app.tts.name}",
        f"Context:      {app.assistant.conversation.turn_count} turns "
        f"(max {config.max_history_turns} turns / {config.max_context_chars} chars)",
        f"Tools:        {len(app.registry.tools)} registered",
        f"Files:        {'; '.join(str(d) for d in config.allowed_dirs)}",
        f"Reminders:    {len(app.store.pending_reminders())} pending",
    ]
