"""Speech-to-text with the ElevenLabs Scribe API (online, uses credits).

Docs: https://elevenlabs.io/docs/api-reference/speech-to-text/convert
"""

import logging

import httpx

log = logging.getLogger(__name__)

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class VoiceError(Exception):
    """A voice step failed. `kind` is one of: config, mic, no_speech,
    auth, quota, rate_limit, bad_audio, network, service."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _detail(response):
    try:
        detail = response.json().get("detail")
    except ValueError:
        return "", response.text[:200]
    if isinstance(detail, dict):
        return str(detail.get("status", "")), str(detail.get("message", ""))
    return "", str(detail or "")


def elevenlabs_error(response, service):
    """Map an ElevenLabs HTTP error response to a VoiceError."""
    status, message = _detail(response)
    code = response.status_code
    # ElevenLabs reports an exhausted quota with HTTP 401, so check it first.
    if "quota" in status or code == 402:
        return VoiceError("quota", "Your ElevenLabs credits/quota are used up.")
    if code == 401:
        if "permission" in (status + message).lower():
            return VoiceError("auth", f"The ElevenLabs API key lacks the {service} permission.")
        return VoiceError("auth", "ElevenLabs rejected the API key. Check ELEVENLABS_API_KEY in .env.")
    if code == 429:
        return VoiceError("rate_limit", "ElevenLabs is rate-limiting requests. Try again shortly.")
    if code in (400, 422):
        return VoiceError("bad_audio", f"ElevenLabs could not process the request: {message or status}")
    return VoiceError("service", f"ElevenLabs {service} error ({code}).")


class ElevenLabsSTT:
    def __init__(self, api_key, model="scribe_v2", language="", http=None, timeout=45.0):
        if not api_key:
            raise VoiceError("config", "Voice input needs ELEVENLABS_API_KEY in .env.")
        self.api_key = api_key
        self.model = model
        self.language = language
        self.http = http or httpx.Client(timeout=timeout)

    def transcribe(self, audio, filename="speech.wav", mime="audio/wav"):
        """Send audio bytes and return the transcribed text."""
        if not audio or len(audio) < 1000:
            raise VoiceError("no_speech", "The recording was too short.")
        data = {"model_id": self.model, "tag_audio_events": "false"}
        if self.language:
            data["language_code"] = self.language
        try:
            response = self.http.post(
                STT_URL,
                headers={"xi-api-key": self.api_key},
                data=data,
                files={"file": (filename, audio, mime)},
            )
        except httpx.TimeoutException as error:
            raise VoiceError("network", "Speech recognition timed out.") from error
        except httpx.HTTPError as error:
            raise VoiceError("network", "Could not reach ElevenLabs. Check your internet connection.") from error

        if response.status_code != 200:
            error = elevenlabs_error(response, "speech-to-text")
            log.warning("STT failed: %s", error.message)
            raise error
        try:
            text = (response.json().get("text") or "").strip()
        except ValueError as error:
            raise VoiceError("service", "ElevenLabs returned an unreadable response.") from error
        if not text:
            raise VoiceError("no_speech", "I didn't catch any speech.")
        return text
