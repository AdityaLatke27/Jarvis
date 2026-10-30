import io
import json
import wave

import httpx
import numpy as np
import pytest

from jarvis.voice.recorder import is_silent, parse_device, to_wav
from jarvis.voice.stt import ElevenLabsSTT, VoiceError
from jarvis.voice.tts import ElevenLabsTTS, FallbackTTS, NullTTS, Speaker, clean_for_speech

AUDIO = b"RIFF" + b"\x00" * 4000


def stt_with(handler):
    return ElevenLabsSTT("key", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_transcribe_success_sends_expected_request():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["xi-api-key"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": " Open notepad. "})

    assert stt_with(handler).transcribe(AUDIO) == "Open notepad."
    assert seen["key"] == "key" and b"scribe_v2" in seen["body"]


@pytest.mark.parametrize("status,body,kind", [
    (401, {"detail": {"status": "invalid_api_key", "message": "bad"}}, "auth"),
    (401, {"detail": {"status": "missing_permissions", "message": "needs speech_to_text permission"}}, "auth"),
    (401, {"detail": {"status": "quota_exceeded", "message": "quota"}}, "quota"),
    (402, {"detail": "payment required"}, "quota"),
    (429, {"detail": {"status": "too_many_concurrent_requests"}}, "rate_limit"),
    (422, {"detail": "invalid audio"}, "bad_audio"),
    (500, {}, "service"),
])
def test_transcribe_errors(status, body, kind):
    with pytest.raises(VoiceError) as raised:
        stt_with(lambda r: httpx.Response(status, json=body)).transcribe(AUDIO)
    assert raised.value.kind == kind


def test_quota_status_detected_on_non_401():
    with pytest.raises(VoiceError) as raised:
        stt_with(lambda r: httpx.Response(400, json={"detail": {"status": "quota_exceeded"}})).transcribe(AUDIO)
    assert raised.value.kind == "quota"


def test_empty_transcript_and_short_audio():
    with pytest.raises(VoiceError) as raised:
        stt_with(lambda r: httpx.Response(200, json={"text": ""})).transcribe(AUDIO)
    assert raised.value.kind == "no_speech"
    with pytest.raises(VoiceError):
        stt_with(lambda r: httpx.Response(200, json={"text": "x"})).transcribe(b"tiny")


def test_network_failure():
    def handler(request):
        raise httpx.ConnectError("offline")
    with pytest.raises(VoiceError) as raised:
        stt_with(handler).transcribe(AUDIO)
    assert raised.value.kind == "network"


def test_missing_key():
    with pytest.raises(VoiceError) as raised:
        ElevenLabsSTT("")
    assert raised.value.kind == "config"


def test_silence_detection_and_wav():
    silence = np.zeros(16000, dtype=np.int16)
    tone = (np.sin(np.linspace(0, 400 * np.pi, 16000)) * 8000).astype(np.int16)
    assert is_silent(silence) and not is_silent(tone)
    with wave.open(io.BytesIO(to_wav(tone))) as wav:
        assert wav.getframerate() == 16000 and wav.getnframes() == 16000
    assert parse_device("") is None and parse_device("2") == 2 and parse_device("USB Mic") == "USB Mic"


def test_clean_for_speech():
    text = "## Title\n**Bold** and `code` see [docs](https://x.y)\n```python\nprint(1)\n```"
    spoken = clean_for_speech(text)
    assert "#" not in spoken and "*" not in spoken and "print" not in spoken and "docs" in spoken
    assert clean_for_speech("word. " * 400).endswith("The rest is on screen.")


def el_tts(handler):
    return ElevenLabsTTS("key", "voice", "eleven_v4_turbo", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_elevenlabs_tts_requests_wav_with_natural_settings():
    def handler(request):
        assert request.url.params["output_format"] == "wav_22050"
        body = json.loads(request.content)
        assert body["model_id"] == "eleven_v4_turbo"
        assert body["voice_settings"]["stability"] == 0.45 and body["voice_settings"]["style"] == 0.25
        return httpx.Response(200, content=b"RIFFdata")
    assert el_tts(handler).synthesize("hello") == b"RIFFdata"


def test_rejected_voice_settings_retried_with_defaults():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        if "voice_settings" in bodies[-1]:
            return httpx.Response(422, json={"detail": "style not supported"})
        return httpx.Response(200, content=b"RIFFok")
    tts = el_tts(handler)
    assert tts.synthesize("hi") == b"RIFFok"
    assert tts.synthesize("again") == b"RIFFok"
    assert len(bodies) == 3  # settings are not retried on every call


def test_tts_errors_mapped():
    with pytest.raises(VoiceError) as raised:
        el_tts(lambda r: httpx.Response(401, json={"detail": {"status": "quota_exceeded"}})).synthesize("x")
    assert raised.value.kind == "quota"


class Backup:
    name = "pyttsx3"

    def synthesize(self, text):
        return b"RIFFoffline"


def test_fallback_to_offline_voice():
    failing = el_tts(lambda r: httpx.Response(401, json={"detail": {"status": "quota_exceeded"}}))
    tts = FallbackTTS(failing, Backup())
    assert tts.name == "elevenlabs"
    assert tts.synthesize("hello") == b"RIFFoffline"
    assert "quota" in tts.last_error
    working = FallbackTTS(el_tts(lambda r: httpx.Response(200, content=b"RIFFonline")), Backup())
    assert working.synthesize("hello") == b"RIFFonline"


def test_null_speaker_is_disabled(tmp_path):
    speaker = Speaker(NullTTS(), tmp_path)
    assert not speaker.enabled
    speaker.speak("hello")  # no error, no audio
    speaker.stop()
