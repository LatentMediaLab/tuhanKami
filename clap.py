import queue
import threading
import time

import numpy as np
import sounddevice as sd
from scipy.signal import butter, sosfilt

# Bandpass filter range that captures hand-clap transients
LOWCUT = 300
HIGHCUT = 3000
SAMPLE_RATE = 44100

"""
Dedicated input device for clap detection (wired mic).
Run: python -c "import sounddevice as sd; print(sd.query_devices())"
to list available devices. None = system default.
"""
CLAP_INPUT: int | str | None = 3


class InlineClapDetector:
    # Detects claps in audio chunks using a bandpass filter and peak detection.
    PEAK = 0.15    # minimum filtered peak to count as a clap
    DEBOUNCE = 0.2 # seconds to ignore after a detected clap (prevents double-counting one clap)
    WINDOW = 0.8    # seconds within which two claps must land to count as a double clap

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        # Pre-compute the bandpass filter coefficients once at startup
        self.sos = butter(4, [LOWCUT, HIGHCUT], btype="band", fs=sample_rate, output="sos")
        self.times: list[float] = []  # timestamps of recent individual claps
        self.last = 0.0               # timestamp of the last accepted clap (for debouncing)

    def clap_in_chunk(self, chunk: np.ndarray) -> bool:
        # True if this audio chunk contains a clap above the peak threshold.
        filtered = sosfilt(self.sos, chunk.flatten())
        peak = float(np.max(np.abs(filtered)))
        now = time.monotonic()
        if peak > self.PEAK and now - self.last > self.DEBOUNCE:
            self.last = now
            return True
        return False

    def feed_double(self, chunk: np.ndarray) -> bool:
        # Returns True when a double clap within WINDOW seconds is detected.
        if self.clap_in_chunk(chunk):
            now = time.monotonic()
            # Keep only claps within the detection window
            self.times = [t for t in self.times if now - t <= self.WINDOW]
            self.times.append(now)
            if len(self.times) >= 2:
                self.times.clear()
                return True
        return False


class ClapRitual:
    """
    Background clap detector using sounddevice.

    Runs a continuous mic stream via sounddevice (no PyAudio). On macOS, Core Audio
    allows concurrent input streams, so recording functions can open their own streams
    alongside this one without conflict.

    Use as a context manager:
        with ClapRitual() as ritual:
            ritual.wait_for_double()

    Events:
      double  — set when a double clap is detected (auto-cleared by wait_for_double)
      abort   — set by the operator to stop the ritual immediately
      paused  — set while the operator is holding the pause key (recording discarded)
    """

    CHUNK_SECS = 0.05  # size of each audio chunk fed to the detector

    def __init__(self) -> None:
        self.double = threading.Event()
        self.abort = threading.Event()
        self.paused = threading.Event()
        self.running = False
        self.thread: threading.Thread | None = None

    def loop(self) -> None:
        # Mic listener thread: feeds chunks to the detector and sets self.double on match.
        q: queue.Queue = queue.Queue()
        detector = InlineClapDetector(SAMPLE_RATE)
        chunk_size = int(SAMPLE_RATE * self.CHUNK_SECS)

        def callback(indata, frames, t, status):
            q.put(indata.copy())

        with sd.InputStream(samplerate=SAMPLE_RATE, device=CLAP_INPUT, channels=1, dtype="float32",
                            blocksize=chunk_size, callback=callback):
            while self.running:
                try:
                    chunk = q.get(timeout=0.1)
                    if detector.feed_double(chunk):
                        self.double.set()
                except queue.Empty:
                    pass

    def start(self) -> None:
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None

    def wait_for_double(self) -> bool:
        # Block until a double clap is heard. Returns False if the ritual is aborted instead.
        self.double.clear()
        while not self.abort.is_set():
            if self.double.wait(timeout=0.1):
                return True
        return False

    def __enter__(self) -> "ClapRitual":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
