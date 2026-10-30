"""HUD server: serves the holographic main screen and talks to it over a
WebSocket on 127.0.0.1 only.

Browser -> server messages (JSON text frames unless noted):
  {"type": "text", "text": "..."}                 typed message
  {"type": "audio", "mime": "audio/webm"}         followed by ONE binary frame (push-to-talk clip)
  {"type": "approve", "id": "...", "approved": true|false}
  {"type": "greet"} | {"type": "clear"} | {"type": "status"}
Server -> browser:
  hello, status, state (idle|transcribing|thinking|speaking), transcript,
  tool, reply, speak (url of a WAV file), approval, notice

Security: the page gets a random per-launch token; the WebSocket requires
it and a localhost Origin, so other websites cannot drive JARVIS.
"""

import asyncio
import json
import logging
import secrets
import threading
import uuid
import webbrowser
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .app import build_app
from .assistant import summarize_outcomes
from .config import PROJECT_ROOT
from .greeting import greet_for
from .voice import VoiceError, clean_for_speech

log = logging.getLogger(__name__)

HUD_DIST = PROJECT_ROOT / "hud" / "dist"
MAX_AUDIO_BYTES = 10_000_000
APPROVAL_TIMEOUT = 120
AUDIO_TYPES = {"audio/webm": "speech.webm", "audio/ogg": "speech.ogg", "audio/wav": "speech.wav",
               "audio/mp4": "speech.m4a", "audio/mpeg": "speech.mp3"}

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>JARVIS</title>
<body style="background:#020407;color:#ffb547;font:16px monospace;padding:40px">
<h1>JARVIS HUD is not built yet</h1><p>Run <b>start_hud.bat</b>, or in a terminal:</p>
<pre>cd hud\nnpm install\nnpm run build</pre><p>then restart <b>python main.py --hud</b>.</p></body>"""


class Hub:
    """Shared state between the WebSocket(s) and worker threads."""

    def __init__(self, jarvis, token):
        self.jarvis = jarvis
        self.token = token
        self.sockets = set()
        self.loop = None
        self.busy = threading.Lock()
        self.approvals = {}
        self.audio = OrderedDict()
        self.backlog = []  # notices produced while no browser is connected

    # ------------------------------------------------------------ sending
    def send(self, message):
        """Broadcast from any thread."""
        if self.loop is None or not self.sockets:
            if message.get("type") in {"notice", "speak"}:
                self.backlog = (self.backlog + [message])[-20:]
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(message), self.loop)

    def take_backlog(self):
        messages, self.backlog = self.backlog, []
        return messages

    async def _broadcast(self, message):
        text = json.dumps(message, default=str)
        for socket in list(self.sockets):
            try:
                await socket.send_text(text)
            except Exception:
                self.sockets.discard(socket)

    def state(self, value):
        self.send({"type": "state", "state": value})

    def status(self):
        app = self.jarvis
        return {
            "model": app.llm.active_model,
            "memory": app.memory.available,
            "memory_reason": app.memory.offline_reason,
            "voice_input": app.stt is not None,
            "tts": app.tts.name,
            "tools": len(app.registry.tools),
            "turns": app.assistant.conversation.turn_count,
            "reminders": len(app.store.pending_reminders()),
            "notes": app.startup_notes,
        }

    # ------------------------------------------------------------ speech
    def speak(self, text):
        spoken = clean_for_speech(text)
        if not spoken or self.jarvis.tts.name == "none":
            return
        try:
            wav = self.jarvis.tts.synthesize(spoken)
        except VoiceError as error:
            self.send({"type": "notice", "level": "warn", "text": f"Voice output failed: {error.message}"})
            return
        if not wav:
            return
        audio_id = uuid.uuid4().hex
        self.audio[audio_id] = wav
        while len(self.audio) > 10:
            self.audio.popitem(last=False)
        self.send({"type": "speak", "url": f"/api/tts/{audio_id}.wav"})

    # ---------------------------------------------------------- approvals
    def approver(self, tool_name, preview):
        approval_id = uuid.uuid4().hex
        event = threading.Event()
        self.approvals[approval_id] = {"event": event, "approved": False}
        self.send({"type": "approval", "id": approval_id, "tool": tool_name, "preview": preview,
                   "timeout": APPROVAL_TIMEOUT})
        event.wait(APPROVAL_TIMEOUT)
        approved = self.approvals.pop(approval_id)["approved"]
        if not event.is_set():
            self.send({"type": "notice", "level": "warn", "text": "Approval timed out; action cancelled."})
        return approved

    def resolve_approval(self, approval_id, approved):
        entry = self.approvals.get(approval_id)
        if entry:
            entry["approved"] = bool(approved)
            entry["event"].set()

    # ------------------------------------------------------------- work
    def handle_text(self, text):
        text = (text or "").strip()[:4000]
        if not text:
            return
        if not self.busy.acquire(blocking=False):
            self.send({"type": "notice", "level": "warn", "text": "Still working on the previous request."})
            return
        try:
            self._answer(text)
        finally:
            self.busy.release()
            self.state("idle")

    def _answer(self, text):
        self.state("thinking")
        result = self.jarvis.assistant.ask(
            text, approver=self.approver,
            on_tool=lambda r: self.send({"type": "tool", **r.as_dict()}),
        )
        self.send({"type": "reply", "text": result.reply, "ok": result.ok, "error": result.error,
                   "summary": summarize_outcomes(result.outcomes)})
        if result.ok or result.error == "tool_limit":
            self.state("speaking")
            self.speak(result.reply)

    def handle_audio(self, audio, mime):
        if not self.busy.acquire(blocking=False):
            self.send({"type": "notice", "level": "warn", "text": "Still working on the previous request."})
            return
        try:
            if self.jarvis.stt is None:
                self.send({"type": "notice", "level": "warn",
                           "text": "Voice input is disabled: add ELEVENLABS_API_KEY to .env."})
                return
            self.state("transcribing")
            base_mime = (mime or "audio/webm").split(";")[0].strip().lower()
            filename = AUDIO_TYPES.get(base_mime, "speech.webm")
            try:
                text = self.jarvis.stt.transcribe(audio, filename, base_mime)
            except VoiceError as error:
                self.send({"type": "notice", "level": "warn", "text": error.message})
                return
            self.send({"type": "transcript", "text": text})
            self._answer(text)
        finally:
            self.busy.release()
            self.state("idle")

    def greet(self):
        """Spoken welcome when the HUD finishes booting (no AI call)."""
        text = greet_for(self.jarvis)
        self.send({"type": "reply", "text": text, "ok": True, "error": None, "summary": "", "greeting": True})
        self.state("speaking")
        self.speak(text)
        self.state("idle")

    def notify_reminder(self, reminder):
        late = " (overdue)" if reminder.get("overdue") else ""
        self.send({"type": "notice", "level": "reminder", "text": f"REMINDER: {reminder['message']}{late}"})
        self.speak(f"Reminder: {reminder['message']}")


def create_server(jarvis, token=None, hud_dir=HUD_DIST):
    token = token or secrets.token_urlsafe(24)
    hub = Hub(jarvis, token)
    api = FastAPI(title="JARVIS HUD", docs_url=None, redoc_url=None, openapi_url=None)
    api.state.hub = hub
    hud_dir = Path(hud_dir)
    port = jarvis.config.hud_port
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}",
                       f"http://{jarvis.config.hud_host}:{port}"}

    if (hud_dir / "assets").is_dir():
        api.mount("/assets", StaticFiles(directory=hud_dir / "assets"), name="assets")

    @api.get("/", response_class=HTMLResponse)
    def index():
        page = hud_dir / "index.html"
        if not page.is_file():
            return HTMLResponse(NOT_BUILT)
        html = page.read_text(encoding="utf-8")
        meta = f'<meta name="jarvis-token" content="{token}" />'
        return HTMLResponse(html.replace("</head>", f"  {meta}\n  </head>", 1),
                            headers={"Cache-Control": "no-store"})

    @api.get("/api/tts/{audio_id}.wav")
    def tts_audio(audio_id: str):
        wav = hub.audio.get(audio_id)
        if wav is None:
            return Response(status_code=404)
        return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @api.websocket("/ws")
    async def websocket(socket: WebSocket):
        origin = socket.headers.get("origin")
        if socket.query_params.get("token") != token or (origin and origin not in allowed_origins):
            log.warning("Rejected WebSocket connection (origin=%s)", origin)
            await socket.close(code=1008)
            return
        await socket.accept()
        hub.loop = asyncio.get_running_loop()
        hub.sockets.add(socket)
        await socket.send_text(json.dumps({"type": "hello", "status": hub.status()}, default=str))
        for queued in hub.take_backlog():
            await socket.send_text(json.dumps(queued, default=str))
        pending_audio_mime = None
        try:
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    data = message["bytes"]
                    if pending_audio_mime is None or len(data) > MAX_AUDIO_BYTES:
                        await socket.send_text(json.dumps(
                            {"type": "notice", "level": "warn", "text": "Audio clip rejected."}))
                    else:
                        asyncio.create_task(asyncio.to_thread(hub.handle_audio, data, pending_audio_mime))
                    pending_audio_mime = None
                    continue
                try:
                    payload = json.loads(message.get("text") or "{}")
                except json.JSONDecodeError:
                    continue
                kind = payload.get("type")
                if kind == "text":
                    asyncio.create_task(asyncio.to_thread(hub.handle_text, str(payload.get("text", ""))))
                elif kind == "greet":
                    asyncio.create_task(asyncio.to_thread(hub.greet))
                elif kind == "audio":
                    pending_audio_mime = str(payload.get("mime") or "audio/webm")
                elif kind == "approve":
                    hub.resolve_approval(str(payload.get("id")), payload.get("approved") is True)
                elif kind == "clear":
                    jarvis.assistant.conversation.clear()
                    await socket.send_text(json.dumps({"type": "notice", "level": "info",
                                                       "text": "Conversation cleared. Memories kept."}))
                elif kind == "status":
                    await socket.send_text(json.dumps({"type": "status", "status": hub.status()}, default=str))
        except WebSocketDisconnect:
            pass
        finally:
            hub.sockets.discard(socket)
            # Deny anything still waiting so worker threads do not hang.
            if not hub.sockets:
                for approval_id in list(hub.approvals):
                    hub.resolve_approval(approval_id, False)

    return api


def run_server(config, open_browser=True, check_models=True):
    import uvicorn

    hub_holder = []
    print("Starting JARVIS HUD...")
    jarvis = build_app(config, notify=lambda r: hub_holder[0].notify_reminder(r) if hub_holder else None,
                       check_models=check_models)
    api = create_server(jarvis)
    hub_holder.append(api.state.hub)
    for note in jarvis.startup_notes:
        print(f"  ! {note}")

    url = f"http://{config.hud_host}:{config.hud_port}/"
    if not (HUD_DIST / "index.html").is_file():
        print("  ! HUD is not built. Run start_hud.bat (or: cd hud && npm install && npm run build).")
    print(f"JARVIS HUD running at {url}  (Ctrl+C to stop)")

    def start_background():
        jarvis.reminders.check_due()
        jarvis.reminders.start()
        if open_browser:
            webbrowser.open(url)

    threading.Timer(1.5, start_background).start()
    try:
        uvicorn.run(api, host=config.hud_host, port=config.hud_port, log_level="warning")
    finally:
        jarvis.shutdown()
