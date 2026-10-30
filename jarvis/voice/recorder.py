"""Push-to-talk microphone recording for the terminal interface."""

import io
import time
import wave

from .stt import VoiceError

SAMPLE_RATE = 16000
SILENCE_RMS = 0.006  # normalized RMS below which a clip counts as silence


def to_wav(samples, sample_rate=SAMPLE_RATE):
    """int16 numpy array -> WAV file bytes."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


def is_silent(samples, threshold=SILENCE_RMS):
    import numpy as np

    if samples.size == 0:
        return True
    floats = samples.astype(np.float32) / 32768.0
    # Use the loudest 50 ms window so a short word in a long clip still counts.
    window = SAMPLE_RATE // 20
    if floats.size < window:
        return float(np.sqrt(np.mean(floats ** 2))) < threshold
    trimmed = floats[: floats.size - floats.size % window].reshape(-1, window)
    return float(np.sqrt(np.mean(trimmed ** 2, axis=1)).max()) < threshold


def parse_device(value):
    value = (value or "").strip()
    if not value:
        return None
    return int(value) if value.isdigit() else value


def record_push_to_talk(device=None, max_seconds=15.0, print_fn=print):
    """Record until Enter/Space is pressed or max_seconds pass. Returns WAV bytes."""
    try:
        import msvcrt
        import numpy as np
        import sounddevice as sd
    except ImportError as error:
        raise VoiceError("mic", f"Microphone recording is unavailable: {error}") from error

    chunks = []

    def callback(data, frames, time_info, status):
        chunks.append(data.copy())

    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                device=parse_device(device), callback=callback)
    except (sd.PortAudioError, ValueError) as error:
        raise VoiceError("mic", f"Could not open the microphone: {error}. Check that a microphone is "
                                "connected and allowed in Windows privacy settings, or set JARVIS_MIC_DEVICE.") from error

    while msvcrt.kbhit():  # discard keys typed before recording
        msvcrt.getwch()
    print_fn(f"  [REC] Listening... press Enter to stop (max {max_seconds:.0f}s).")
    start = time.monotonic()
    with stream:
        while time.monotonic() - start < max_seconds:
            if msvcrt.kbhit() and msvcrt.getwch() in "\r\n ":
                break
            time.sleep(0.03)
        else:
            print_fn("  [REC] Maximum recording length reached.")

    if not chunks:
        raise VoiceError("mic", "The microphone produced no audio.")
    samples = np.concatenate(chunks).reshape(-1)
    if samples.size < SAMPLE_RATE * 0.3:
        raise VoiceError("no_speech", "The recording was too short.")
    if is_silent(samples):
        raise VoiceError("no_speech", "I only heard silence. Check your microphone level.")
    return to_wav(samples)
