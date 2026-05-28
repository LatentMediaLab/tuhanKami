import queue
import subprocess
import threading
import time as _time
from typing import TYPE_CHECKING

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import resample as sp_resample

if TYPE_CHECKING:
    from clap import ClapRitual

SAMPLE_RATE = 16000  # Whisper's native rate — no resampling needed


def record_audio(duration_seconds: int, filename: str) -> None:
    print(f"  [Recording {duration_seconds}s...]")
    audio = sd.rec(
        int(duration_seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    sf.write(filename, audio, SAMPLE_RATE)


def record_until_enter(filename: str) -> None:
    print("  [Recording... press Enter to stop]")
    chunks = []

    def callback(indata, frames, time, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback):
        input()

    audio = np.concatenate(chunks, axis=0)
    sf.write(filename, audio, SAMPLE_RATE)


def record_voice_activity(
    filename: str,
    silence_duration: float = 1.5,
    max_duration: float = 60.0,
) -> None:
    """Records from when speech starts until the speaker goes silent."""
    CHUNK_SECS = 0.05  # 50ms chunks
    CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_SECS)
    silence_needed = int(silence_duration / CHUNK_SECS)
    max_chunks = int(max_duration / CHUNK_SECS)

    # Calibrate: sample ambient noise floor for 2.0s
    cal = sd.rec(int(2.0 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    noise_rms = float(np.sqrt(np.mean(cal ** 2)))
    threshold = max(noise_rms * 2, 0.001)  

    q = queue.Queue()

    def callback(indata, frames, time, status):
        q.put(indata.copy())

    print("  [Listening...]")

    speech_chunks = []
    speech_started = False
    silence_count = 0
    total = 0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=CHUNK_SIZE, callback=callback):
        while total < max_chunks:
            chunk = q.get()
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            total += 1

            if not speech_started:
                if rms > threshold:
                    print("  [Speaking...]")
                    speech_started = True
                    speech_chunks.append(chunk)
            else:
                speech_chunks.append(chunk)
                if rms > threshold:
                    silence_count = 0
                else:
                    silence_count += 1
                    if silence_count >= silence_needed:
                        break

    if speech_chunks:
        audio = np.concatenate(speech_chunks, axis=0)
        sf.write(filename, audio, SAMPLE_RATE)


def pitch_shift(audio: np.ndarray, semitones: float = -2.0) -> np.ndarray:
    """Shift pitch by resampling. Negative = deeper."""
    factor = 2.0 ** (semitones / 12.0)
    stretched = np.asarray(sp_resample(audio, int(len(audio) / factor)))
    return np.asarray(sp_resample(stretched, len(audio)))


def add_reverb(audio: np.ndarray, delay_samples: int = 800, decay: float = 0.38, echoes: int = 5) -> np.ndarray:
    """Simple delay-line reverb. delay_samples=800 ≈ 50ms at 16kHz."""
    result = audio.copy()
    for i in range(1, echoes + 1):
        start = i * delay_samples
        if start < len(audio):
            result[start:] += audio[:len(audio) - start] * (decay ** i)
    return np.clip(result, -1.0, 1.0)


def process_entity_audio(pcm_bytes: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    audio = audio.flatten()
    audio = pitch_shift(audio, semitones=-2.0)
    return add_reverb(audio)


def play_pcm(pcm_bytes: bytes) -> None:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(audio, samplerate=SAMPLE_RATE)
    sd.wait()


def play_entity_pcm(pcm_bytes: bytes) -> None:
    """Plays entity TTS with pitch shift and reverb applied."""
    sd.play(process_entity_audio(pcm_bytes), samplerate=SAMPLE_RATE)
    sd.wait()


def is_silent(filename: str, threshold: float = 0.001) -> bool:
    data, _ = sf.read(filename)
    return float(np.abs(data).mean()) < threshold


def load_bell(bell_path: str) -> np.ndarray:
    """Load a bell file as a mono float32 array at SAMPLE_RATE."""
    from scipy.signal import resample as sp_resample
    data, rate = sf.read(bell_path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        data = np.asarray(sp_resample(data, int(len(data) * SAMPLE_RATE / rate)))
    return data


def play_bell(
    bell_path: str,
    *,
    times: int = 1,
    overlap_secs_min: float = 0.0,
    overlap_secs_max: float = 0.0,
    greeting_pcm: bytes | None = None,
    greeting_offset_secs: float = 2.0,
) -> None:
    """Mix and play a bell with optional repetition and greeting overlay.

    times / overlap_secs: ring the bell `times` times, each starting
        overlap_secs after the previous (ignored when times=1).
    greeting_pcm: entity PCM to overlay, starting at greeting_offset_secs.
    """
    bell = load_bell(bell_path)
    overlap_secs = np.random.uniform(overlap_secs_min, overlap_secs_max)
    step = int(overlap_secs * SAMPLE_RATE)
    total = step * (times - 1) + len(bell)

    greeting: np.ndarray | None = None
    offset = 0
    if greeting_pcm is not None:
        greeting = process_entity_audio(greeting_pcm)
        offset = int(greeting_offset_secs * SAMPLE_RATE)
        total = max(total, offset + len(greeting))

    mixed = np.zeros(total, dtype="float32")
    for i in range(times):
        mixed[i * step: i * step + len(bell)] += bell

    if greeting is not None:
        mixed[offset: offset + len(greeting)] += greeting

    peak = float(np.max(np.abs(mixed)))
    if peak > 1.0:
        mixed /= peak
    sd.play(mixed, samplerate=SAMPLE_RATE)
    sd.wait()


# ── Clap-driven recording and playback ───────────────────────────────────────

def record_until_double_clap(ritual: "ClapRitual", filename: str) -> None:
    """Records continuously until ClapRitual detects a double clap."""
    chunks: list[np.ndarray] = []
    q: queue.Queue = queue.Queue()
    CHUNK_SIZE = int(SAMPLE_RATE * 0.05)

    ritual._double.clear()

    def callback(indata, frames, time, status):
        q.put(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=CHUNK_SIZE, callback=callback):
        while True:
            if ritual._double.is_set():
                break
            try:
                chunks.append(q.get(timeout=0.1))
            except queue.Empty:
                pass

    if chunks:
        sf.write(filename, np.concatenate(chunks, axis=0), SAMPLE_RATE)


def record_question(ritual: "ClapRitual", filename: str) -> bool:
    """
    Records speech using VAD. ClapRitual runs concurrently on the same mic.
    Returns True if a double clap is detected (signals end of ritual).
    """
    CHUNK_SIZE = int(SAMPLE_RATE * 0.05)
    SILENCE_CHUNKS = int(1.5 / 0.05)  # 30 chunks @ 50ms each = 1.5s of silence
    SPEECH_ONSET_CHUNKS = 2
    CAL_CHUNKS = 20  # ~1 second of inline calibration before listening

    ritual._double.clear()

    q: queue.Queue = queue.Queue()
    chunks: list[np.ndarray] = []
    cal_rms: list[float] = []
    speech_started = False
    onset_count = 0
    silence_count = 0
    vad_threshold = 0.001

    def callback(indata, frames, time, status):
        q.put(indata.copy())

    print("  [Listening...]")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=CHUNK_SIZE, callback=callback):
        while True:
            if ritual._double.is_set():
                return True

            try:
                chunk = q.get(timeout=0.1)
            except queue.Empty:
                continue

            if len(cal_rms) < CAL_CHUNKS:
                cal_rms.append(float(np.sqrt(np.mean(chunk ** 2))))
                if len(cal_rms) == CAL_CHUNKS:
                    vad_threshold = max(float(np.mean(cal_rms)) * 2, 0.001)
                continue

            if not speech_started:
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                if rms > vad_threshold:
                    onset_count += 1
                    if onset_count >= SPEECH_ONSET_CHUNKS:
                        speech_started = True
                        print("  [Speaking...]")
                        chunks.append(chunk)
                else:
                    onset_count = 0
            else:
                chunks.append(chunk)
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                if rms > vad_threshold:
                    silence_count = 0
                else:
                    silence_count += 1
                    if silence_count >= SILENCE_CHUNKS:
                        break

    if chunks:
        sf.write(filename, np.concatenate(chunks, axis=0), SAMPLE_RATE)
    return False


def play_entity_pcm_interruptible(pcm_bytes: bytes, ritual: "ClapRitual") -> None:
    """Plays entity audio with effects. A double clap interrupts playback."""
    audio = process_entity_audio(pcm_bytes)
    done = threading.Event()

    def _play() -> None:
        sd.play(audio, samplerate=SAMPLE_RATE)
        sd.wait()
        done.set()

    ritual._double.clear()
    t = threading.Thread(target=_play, daemon=True)
    t.start()

    while not done.is_set():
        if ritual._double.is_set():
            sd.stop()
            break
        _time.sleep(0.02)

    t.join()
