"""Clipboard tools.

Reading the clipboard sends its contents to the AI provider, and the
clipboard often holds passwords, so reading requires approval and
secret-looking text is withheld. Writing only replaces clipboard text.
"""

from ..memory.manager import contains_secret
from .registry import Tool, ToolError

MAX_CLIPBOARD_CHARS = 4000


class Clipboard:
    def __init__(self, backend=None):
        if backend is None:
            import pyperclip
            backend = pyperclip
        self.backend = backend

    def read(self):
        try:
            text = self.backend.paste() or ""
        except Exception as error:
            raise ToolError(f"Clipboard is not available: {type(error).__name__}") from error
        if not text:
            return {"text": "", "empty": True}
        if contains_secret(text):
            raise ToolError("The clipboard appears to contain a password, key or token, so it was not read.")
        return {"text": text[:MAX_CLIPBOARD_CHARS], "truncated": len(text) > MAX_CLIPBOARD_CHARS}

    def write(self, text):
        if len(text) > 100_000:
            raise ToolError("Text is too long for the clipboard tool.")
        try:
            self.backend.copy(text)
            stored = self.backend.paste()
        except Exception as error:
            raise ToolError(f"Clipboard is not available: {type(error).__name__}") from error
        if stored != text:
            raise ToolError("Clipboard write could not be verified.")
        return {"copied": True, "characters": len(text)}


def build_clipboard_tools(clipboard):
    return [
        Tool("read_clipboard", "Read text currently on the clipboard (the user must approve).",
             {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
             clipboard.read, approval=lambda args: "Let JARVIS read your clipboard and send it to the AI model?"),
        Tool("copy_to_clipboard", "Copy text to the clipboard, replacing what is there.",
             {"type": "object", "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 100000}},
              "required": ["text"], "additionalProperties": False},
             clipboard.write),
    ]
