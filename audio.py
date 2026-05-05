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
    from buttons import ButtonState

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


def _pitch_shift(audio: np.ndarray, semitones: float = -2.0) -> np.ndarray:
    """Shift pitch by resampling. Negative = deeper."""
    factor = 2.0 ** (semitones / 12.0)
    stretched = np.asarray(sp_resample(audio, int(len(audio) / factor)))
    return np.asarray(sp_resample(stretched, len(audio)))


def _add_reverb(audio: np.ndarray, delay_samples: int = 800, decay: float = 0.38, echoes: int = 5) -> np.ndarray:
    """Simple delay-line reverb. delay_samples=800 ≈ 50ms at 16kHz."""
    result = audio.copy()
    for i in range(1, echoes + 1):
        start = i * delay_samples
        if start < len(audio):
            result[start:] += audio[:len(audio) - start] * (decay ** i)
    return np.clip(result, -1.0, 1.0)


def _process_oracle_audio(pcm_bytes: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    audio = audio.flatten()
    audio = _pitch_shift(audio, semitones=-2.0)
    return _add_reverb(audio)


def play_pcm(pcm_bytes: bytes) -> None:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    sd.play(audio, samplerate=SAMPLE_RATE)
    sd.wait()


def play_oracle_pcm(pcm_bytes: bytes) -> None:
    """Plays oracle TTS with pitch shift and reverb applied."""
    sd.play(_process_oracle_audio(pcm_bytes), samplerate=SAMPLE_RATE)
    sd.wait()


def is_silent(filename: str, threshold: float = 0.001) -> bool:
    data, _ = sf.read(filename)
    return float(np.abs(data).mean()) < threshold


def play_bell(bell_path: str, times: int = 1, gap: float = 0.5) -> None:
    for i in range(times):
        subprocess.run(["afplay", bell_path], check=False)
        if i < times - 1:
            _time.sleep(gap)


# ── Button-driven recording and playback ─────────────────────────────────────

def record_push_to_talk(state: "ButtonState", filename: str) -> None:
    """Records while all macro pad keys are held; stops the moment any is released."""
    from buttons import read_char  # type: ignore

    chunks: list[np.ndarray] = []
    q: queue.Queue = queue.Queue()

    def callback(indata, frames, time, status):
        q.put(indata.copy())

    print("  [Recording... release to send]")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback):
        while state.all_held:
            ch = read_char(timeout=0.01)
            if ch:
                state.update(ch)
            while not q.empty():
                chunks.append(q.get_nowait())
        # Short drain to catch the last buffer
        _time.sleep(0.05)
        while not q.empty():
            chunks.append(q.get_nowait())

    if chunks:
        audio = np.concatenate(chunks, axis=0)
        sf.write(filename, audio, SAMPLE_RATE)


def record_until_double_clap(state: "ButtonState", filename: str) -> None:
    """Records continuously until a second double-clap is detected."""
    from buttons import read_char, ClapDetector  # type: ignore

    chunks: list[np.ndarray] = []
    q: queue.Queue = queue.Queue()
    detector = ClapDetector()

    def callback(indata, frames, time, status):
        q.put(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback):
        while True:
            ch = read_char(timeout=0.02)
            if ch:
                state.update(ch)
            while not q.empty():
                chunks.append(q.get_nowait())
            if detector.update(state):
                break

    if chunks:
        audio = np.concatenate(chunks, axis=0)
        sf.write(filename, audio, SAMPLE_RATE)


def play_oracle_pcm_interruptible(pcm_bytes: bytes, state: "ButtonState") -> None:
    """Plays oracle audio with effects. Pressing all 3 keys interrupts playback."""
    from buttons import read_char  # type: ignore

    audio = _process_oracle_audio(pcm_bytes)

    done = threading.Event()

    def _play() -> None:
        sd.play(audio, samplerate=SAMPLE_RATE)
        sd.wait()
        done.set()

    t = threading.Thread(target=_play, daemon=True)
    t.start()

    while not done.is_set():
        ch = read_char(timeout=0.02)
        if ch:
            state.update(ch)
        if state.all_held:
            sd.stop()
            break

    t.join()
