"""Text-to-speech.

Engines:
- pyttsx3: offline Windows SAPI voices (free). Voice and rate configurable.
- elevenlabs: online neural voices (uses ElevenLabs credits).
- none: silent.

Every engine produces WAV bytes, so the same audio can be played on the
PC (terminal mode) or sent to the HUD in the browser.
"""

import logging
import queue
import re
import tempfile
import threading
import uuid
from pathlib import Path

import httpx

from .stt import VoiceError, elevenlabs_error

log = logging.getLogger(__name__)

MAX_SPOKEN_CHARS = 1200


def clean_for_speech(text):
    """Remove markdown and code so the voice does not read symbols aloud."""
    text = re.sub(r"```.*?```", " (code shown on screen) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~|>#]+", "", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        text = cut[: cut.rfind(". ") + 1 or MAX_SPOKEN_CHARS] + " The rest is on screen."
    return text


class NullTTS:
    name = "none"

    def synthesize(self, text):
        return None


class Pyttsx3TTS:
    """Runs one SAPI engine on a dedicated thread (COM objects are thread-bound)."""

    name = "pyttsx3"

    def __init__(self, voice="", rate=185, temp_dir=None):
        self.voice = voice
        self.rate = rate
        self.temp_dir = Path(temp_dir or tempfile.gettempdir())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.jobs = queue.Queue()
        self.ready = threading.Event()
        self.init_error = None
        self.voices = []
        threading.Thread(target=self._worker, name="tts", daemon=True).start()
        self.ready.wait(15)
        if self.init_error:
            raise VoiceError("config", f"Offline text-to-speech is unavailable: {self.init_error}")

    def _worker(self):
        try:
            try:
                import comtypes
                comtypes.CoInitialize()
            except ImportError:
                pass
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", self.rate)
            self.voices = [v.name for v in engine.getProperty("voices")]
            if self.voice:
                match = next((v for v in engine.getProperty("voices")
                              if self.voice.lower() in v.name.lower()), None)
                if match:
                    engine.setProperty("voice", match.id)
                else:
                    log.warning("TTS voice '%s' not found; using default", self.voice)
        except Exception as error:
            self.init_error = f"{type(error).__name__}: {error}"
            self.ready.set()
            return
        self.ready.set()
        while True:
            text, reply = self.jobs.get()
            path = self.temp_dir / f"tts_{uuid.uuid4().hex}.wav"
            try:
                engine.save_to_file(text, str(path))
                engine.runAndWait()
                reply.put(path.read_bytes())
            except Exception as error:
                reply.put(VoiceError("service", f"Speech synthesis failed: {error}"))
            finally:
                path.unlink(missing_ok=True)

    def synthesize(self, text):
        reply = queue.Queue()
        self.jobs.put((text, reply))
        try:
            result = reply.get(timeout=60)
        except queue.Empty:
            raise VoiceError("service", "Speech synthesis timed out.")
        if isinstance(result, Exception):
            raise result
        return result


class ElevenLabsTTS:
    """Neural voices. Lower stability and some style sound more human and
    expressive; higher stability sounds steadier but flatter."""

    name = "elevenlabs"

    def __init__(self, api_key, voice_id, model, speed=1.0, stability=0.45, style=0.25, http=None):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.voice_settings = {
            "stability": stability,
            "similarity_boost": 0.8,
            "style": style,
            "use_speaker_boost": True,
            "speed": speed,
        }
        self.settings_supported = True
        self.http = http or httpx.Client(timeout=45)

    def _post(self, text, with_settings):
        body = {"text": text, "model_id": self.model}
        if with_settings:
            body["voice_settings"] = self.voice_settings
        try:
            return self.http.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
                params={"output_format": "wav_22050"},
                headers={"xi-api-key": self.api_key},
                json=body,
            )
        except httpx.HTTPError as error:
            raise VoiceError("network", "Could not reach ElevenLabs for speech output.") from error

    def synthesize(self, text):
        response = self._post(text, self.settings_supported)
        if response.status_code in (400, 422) and self.settings_supported:
            # Some models reject certain voice settings; retry once with the voice's defaults.
            log.warning("ElevenLabs rejected voice settings for %s; using voice defaults", self.model)
            self.settings_supported = False
            response = self._post(text, False)
        if response.status_code != 200:
            raise elevenlabs_error(response, "text-to-speech")
        return response.content


class FallbackTTS:
    """Use the primary engine; if it fails (quota, network...), speak with the backup."""

    def __init__(self, primary, backup):
        self.primary = primary
        self.backup = backup
        self.name = primary.name
        self.last_error = None

    def synthesize(self, text):
        try:
            return self.primary.synthesize(text)
        except VoiceError as error:
            if error.message != self.last_error:
                log.warning("%s voice failed (%s); using %s", self.primary.name, error.message, self.backup.name)
            self.last_error = error.message
            return self.backup.synthesize(text)


def make_tts(config):
    """Create the configured engine. ElevenLabs falls back to the offline voice."""
    if config.tts_engine == "none":
        return NullTTS()
    try:
        offline = Pyttsx3TTS(config.tts_voice, config.tts_rate, config.data_dir / "tts")
    except VoiceError as error:
        log.warning("%s", error.message)
        offline = NullTTS()
    if config.tts_engine == "elevenlabs":
        online = ElevenLabsTTS(config.elevenlabs_api_key, config.elevenlabs_voice_id, config.elevenlabs_tts_model,
                               config.elevenlabs_tts_speed, config.elevenlabs_tts_stability,
                               config.elevenlabs_tts_style)
        return FallbackTTS(online, offline)
    return offline


class Speaker:
    """Speaks text on this PC in the background; stop() interrupts."""

    def __init__(self, engine, cache_dir):
        self.engine = engine
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._generation = 0
        self._lock = threading.Lock()
        self.last_error = None

    @property
    def enabled(self):
        return not isinstance(self.engine, NullTTS)

    def speak(self, text):
        if not self.enabled:
            return
        spoken = clean_for_speech(text)
        if not spoken:
            return
        self.stop()
        with self._lock:
            self._generation += 1
            generation = self._generation
        threading.Thread(target=self._run, args=(spoken, generation), daemon=True).start()

    def _run(self, text, generation):
        try:
            audio = self.engine.synthesize(text)
        except VoiceError as error:
            self.last_error = error.message
            log.warning("TTS failed: %s", error.message)
            return
        if not audio or generation != self._generation:
            return  # stopped while synthesizing
        path = self.cache_dir / "speech.wav"
        try:
            import winsound
            winsound.PlaySound(None, 0)
            path.write_bytes(audio)
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except (ImportError, RuntimeError, OSError) as error:
            self.last_error = f"Audio playback failed: {error}"
            log.warning(self.last_error)

    def stop(self):
        with self._lock:
            self._generation += 1
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except (ImportError, RuntimeError):
            pass
