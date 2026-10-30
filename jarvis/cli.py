"""Terminal interface: typed chat, push-to-talk voice and commands."""

import logging

from .app import status_lines
from .assistant import summarize_outcomes
from .greeting import greet_for
from .tools.registry import ToolError
from .voice import VoiceError
from .voice.recorder import record_push_to_talk

log = logging.getLogger(__name__)

HELP = """
Commands:
  help              show this help
  status            show model, memory, voice and tool status
  clear             forget this conversation (saved memories are kept)
  memories [page]   list saved memories, 10 per page
  reminders         list pending reminders and timers
  tasks             list recent task plans
  v  or  voice      push-to-talk: speak, then press Enter
  stop              stop JARVIS speaking
  exit              shut down (also: quit, shutdown)
Anything else is sent to JARVIS.
""".strip()

EXIT_WORDS = {"exit", "quit", "shutdown"}


def terminal_approver(tool_name, preview):
    print(f"\n  APPROVAL NEEDED ({tool_name})")
    for line in preview.splitlines():
        print(f"    {line}")
    try:
        answer = input("  Approve? [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def show_memories(app, page):
    try:
        result = app.memory.inspect(limit=10, offset=(page - 1) * 10)
    except ToolError as error:
        print(f"  {error}")
        return
    if not result["memories"]:
        print("  No memories on this page.")
        return
    for record in result["memories"]:
        key = f" key={record['key']}" if record.get("key") else ""
        status = f" ({record['status']})" if record.get("status") else ""
        print(f"  #{record['id']} [{record['category']}{key}{status}] {record['content']}")
    if result["more_may_exist"]:
        print(f"  ... type 'memories {page + 1}' for more")


def show_reminders(app):
    pending = app.reminders.list()["pending"]
    if not pending:
        print("  No pending reminders.")
    for item in pending:
        print(f"  #{item['id']} {item['due_local']} - {item['message']}")


def show_tasks(app):
    tasks = app.store.list_tasks(limit=5)
    if not tasks:
        print("  No task plans yet.")
    for task in tasks:
        print(f"  Task #{task['id']} [{task['status']}] {task['title']}")
        for step in task["steps"]:
            note = f" - {step['note']}" if step.get("note") else ""
            print(f"     {step['step']}. [{step['status']}] {step['description']}{note}")


def answer(app, text):
    result = app.assistant.ask(text, approver=terminal_approver)
    label = "JARVIS" if result.ok else "JARVIS (error)"
    print(f"\n{label}: {result.reply}")
    summary = summarize_outcomes(result.outcomes)
    if summary:
        print(f"  {summary}")
    if result.ok or result.error == "tool_limit":
        app.speaker.speak(result.reply)


def listen(app):
    if app.stt is None:
        print("  Voice input is disabled. Add ELEVENLABS_API_KEY to .env (typing still works).")
        return None
    app.speaker.stop()
    try:
        audio = record_push_to_talk(app.config.mic_device, app.config.max_record_seconds)
        print("  Transcribing...")
        text = app.stt.transcribe(audio)
    except VoiceError as error:
        print(f"  Voice: {error.message}")
        return None
    print(f"\nYou (voice): {text}")
    return text


def run_terminal(app):
    print("=" * 50)
    print("JARVIS V3 - type 'help' for commands")
    print("=" * 50)
    for note in app.startup_notes:
        print(f"  ! {note}")
    greeting = greet_for(app)
    print(f"\nJARVIS: {greeting}")
    app.speaker.speak(greeting)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nJARVIS: Shutting down.")
            break
        if not user_input:
            continue

        command, _, argument = user_input.partition(" ")
        command = command.casefold()
        app.speaker.stop()

        try:
            if command in EXIT_WORDS and not argument:
                print("\nJARVIS: Shutting down. See you later.")
                break
            if command == "help" and not argument:
                print(HELP)
            elif command == "status" and not argument:
                for line in status_lines(app):
                    print(f"  {line}")
            elif command == "clear" and not argument:
                app.assistant.conversation.clear()
                print("  Conversation cleared. Saved memories were not touched.")
            elif command == "memories" and (not argument or argument.isdigit()):
                show_memories(app, max(1, int(argument or 1)))
            elif command == "reminders" and not argument:
                show_reminders(app)
            elif command == "tasks" and not argument:
                show_tasks(app)
            elif command == "stop" and not argument:
                pass  # speech already stopped above
            elif command in {"v", "voice"} and not argument:
                text = listen(app)
                if text:
                    answer(app, text)
            else:
                answer(app, user_input)
        except KeyboardInterrupt:
            print("\n  Interrupted.")
        except Exception as error:  # keep the assistant running
            log.exception("Unexpected error")
            print(f"\nJARVIS: Something went wrong ({type(error).__name__}). Details are in logs/jarvis.log.")


def reminder_printer(speaker_holder):
    """Notification callback for terminal mode."""

    def notify(reminder):
        late = " (overdue - it came due while JARVIS was off)" if reminder.get("overdue") else ""
        print(f"\n\n  >>> REMINDER #{reminder['id']}: {reminder['message']}{late}\nYou: ", end="", flush=True)
        if speaker_holder:
            speaker_holder[0].speak(f"Reminder: {reminder['message']}")

    return notify
