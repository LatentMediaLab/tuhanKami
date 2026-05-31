import queue
import threading
import time

import numpy as np
import sounddevice as sd
from scipy.signal import butter, sosfilt

LOWCUT = 300
HIGHCUT = 3000
SAMPLE_RATE = 16000


class InlineClapDetector:
    """Detects claps in audio chunks using a bandpass filter and peak detection."""
    PEAK = 0.2
    DEBOUNCE = 0.15
    WINDOW = 0.8

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self._sos = butter(4, [LOWCUT, HIGHCUT], btype="band", fs=sample_rate, output="sos")
        self._times: list[float] = []
        self._last = 0.0

    def _clap_in_chunk(self, chunk: np.ndarray) -> bool:
        filtered = sosfilt(self._sos, chunk.flatten())
        peak = float(np.max(np.abs(filtered)))
        now = time.monotonic()
        if peak > self.PEAK and now - self._last > self.DEBOUNCE:
            self._last = now
            return True
        return False

    def feed_double(self, chunk: np.ndarray) -> bool:
        """Returns True when a double clap within WINDOW seconds is detected."""
        if self._clap_in_chunk(chunk):
            now = time.monotonic()
            self._times = [t for t in self._times if now - t <= self.WINDOW]
            self._times.append(now)
            if len(self._times) >= 2:
                self._times.clear()
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
    """

    CHUNK_SECS = 0.05

    def __init__(self) -> None:
        self._double = threading.Event()
        self.abort = threading.Event()
        self._running = False
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        q: queue.Queue = queue.Queue()
        detector = InlineClapDetector(SAMPLE_RATE)
        chunk_size = int(SAMPLE_RATE * self.CHUNK_SECS)

        def callback(indata, frames, time, status):
            q.put(indata.copy())

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            blocksize=chunk_size, callback=callback):
            while self._running:
                try:
                    chunk = q.get(timeout=0.1)
                    if detector.feed_double(chunk):
                        self._double.set()
                except queue.Empty:
                    pass

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def wait_for_double(self) -> bool:
        """Block until a double clap is heard. Returns False if aborted."""
        self._double.clear()
        while not self.abort.is_set():
            if self._double.wait(timeout=0.1):
                return True
        return False

    def __enter__(self) -> "ClapRitual":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
