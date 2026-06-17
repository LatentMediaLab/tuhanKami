from __future__ import annotations

import queue
import random
import threading
import time
from typing import TYPE_CHECKING

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import resample as sp_resample

if TYPE_CHECKING:
    from collections.abc import Callable
    from clap import ClapRitual

# Internal sample rate used for all processing and playback (except prayer recording)
SAMPLE_RATE = 16000
# Prayer recording uses 44100 Hz for better voice clone quality
PRAYER_SAMPLE_RATE = 44100
# sounddevice device index for the main bone-conducting headset output
MAIN_OUTPUT: int | str | None = 3

# Each tuple: (device_index, channel_index, total_channels_on_stream)
# channel_index selects L (0) or R (1) within a stereo pair.
ECHO_OUTPUTS: list[tuple[int | str | None, int, int]] = [
    (5, 0, 2),  # speaker 1
    (5, 1, 2),  # speaker 2
    #(5, 0, 2),  # speaker 3
    #(5, 1, 2),  # speaker 4
    #(5, 0, 2),  # speaker 5
    #(5, 1, 2),  # speaker 6
]


def pitch_shift(audio: np.ndarray, semitones: float = -2.0) -> np.ndarray:
    # Shift pitch by resampling: stretch then compress back to original length.
    factor = 2.0 ** (semitones / 12.0)
    stretched = np.asarray(sp_resample(audio, int(len(audio) / factor)))
    return np.asarray(sp_resample(stretched, len(audio)))


def add_reverb(audio: np.ndarray, delay_samples: int = 800, decay: float = 0.38, echoes: int = 5) -> np.ndarray:
    # Simple delay-line reverb: sum N copies of the signal, each delayed and attenuated.
    result = audio.copy()
    for i in range(1, echoes + 1):
        start = i * delay_samples
        if start < len(audio):
            result[start:] += audio[:len(audio) - start] * (decay ** i)
    return np.clip(result, -1.0, 1.0)


def process_entity_audio(pcm_bytes: bytes) -> np.ndarray:
    # Convert raw PCM bytes → pitch-shifted + reverbed float32 array (the Entity timbre).
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    audio = audio.flatten()
    audio = pitch_shift(audio, semitones=-2.0)
    return add_reverb(audio)


def is_silent(filename: str, threshold: float = 0.001) -> bool:
    # True if the file's average amplitude is below threshold (nothing useful captured).
    data, _ = sf.read(filename)
    return float(np.abs(data).mean()) < threshold


def load_bell(bell_path: str) -> np.ndarray:
    # Load a bell audio file, mix to mono, and resample to SAMPLE_RATE.
    data, rate = sf.read(bell_path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        data = np.asarray(sp_resample(data, int(len(data) * SAMPLE_RATE / rate)))
    return data


def compress_for_ivc(src_wav: str, dst_wav: str, target_rate: int = 22050) -> None:
    # Resample prayer WAV to 16-bit PCM at target_rate for ElevenLabs IVC upload (11 MB limit).
    data, rate = sf.read(src_wav, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if rate != target_rate:
        data = np.asarray(sp_resample(data, int(len(data) * target_rate / rate)))
    sf.write(dst_wav, data, target_rate, subtype="PCM_16")


def trim_trailing_silence(audio: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    # Remove silent samples from the end of an array, keeping up to the last loud sample.
    active = np.where(np.abs(audio) > threshold)[0]
    if len(active) == 0:
        return audio
    return audio[:active[-1] + 1]


def reverse_reverb(
    audio: np.ndarray,
    delay_samples: int = 888,
    decay: float = 0.50,
    echoes: int = 25,
    pre_pad_secs: float = 0.15,
    post_pad_secs: float = 1.5,
    fade_lead_secs: float = 3.0,
) -> np.ndarray:
    """
    Swell-in / fade-out effect via time-reversed reverb.

    How it works:
      1. Trim trailing TTS silence so the fade anchors to real speech content.
      2. Reverse the audio, apply heavy reverb (which now bleeds backward in time).
      3. Reverse again — reverb energy now builds up *into* the speech (swell-in).
      4. Apply a cosine fade starting fade_lead_secs before speech ends, fading
         through the post-pad to silence (natural trailing decay).
    """
    pre = np.zeros(int(pre_pad_secs * SAMPLE_RATE), dtype="float32")
    post = np.zeros(int(post_pad_secs * SAMPLE_RATE), dtype="float32")
    speech = trim_trailing_silence(audio.astype("float32"))
    padded = np.concatenate([pre, speech, post])
    # Reverb applied to the reversed signal so energy bleeds forward (into the past)
    wet = add_reverb(padded[::-1].copy(), delay_samples=delay_samples, decay=decay, echoes=echoes)
    result = wet[::-1].copy()
    # Cosine fade from speech end through post-pad
    speech_end = len(pre) + len(speech)
    fade_start = max(len(pre), speech_end - int(fade_lead_secs * SAMPLE_RATE))
    fade_len = len(result) - fade_start
    if fade_len > 0:
        result[fade_start:] *= np.cos(np.linspace(0.0, np.pi / 2, fade_len, dtype="float32"))
    return result


def build_main_audio(main_pcm: bytes) -> np.ndarray:
    # Convert main-voice PCM bytes → reverse-reverb float32 array for bell overlay.
    return reverse_reverb(
        np.frombuffer(main_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    )


class SpeakerPlayer:
    """
    Persistent output stream for one speaker channel with layered mixing.

    Layers play *once* (not looped) — when a clip ends it is removed.
    reverse_reverb's built-in post-pad provides natural fade-out decay.
    When a new clip arrives on an occupied speaker (halve=True), existing
    layer volumes are multiplied by 0.4 and layers below VOL_THRESHOLD are pruned.
    master is a shared list[float] so BackgroundEchoPlayer.set_volume() is
    reflected here without any locking overhead.
    """

    CHUNK = int(SAMPLE_RATE * 0.05)   # 50ms write buffer
    MAX_LAYERS = 4                     # oldest layer dropped if exceeded
    VOL_THRESHOLD = 0.05              # prune layers quieter than this

    def __init__(
        self,
        device: int | str | None,
        channel: int,
        num_channels: int,
        master: list,
    ) -> None:
        self.device = device
        self.channel = channel          # which channel index to write into (0=L, 1=R)
        self.num_channels = num_channels
        self.master = master            # shared [float] — index 0 is the volume multiplier
        self.pending: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.has_audio = False          # True while any layer is playing

    def is_empty(self) -> bool:
        # True when no audio layers are currently playing.
        return not self.has_audio

    def feed(self, audio: np.ndarray, halve: bool = False) -> None:
        # Queue a new audio clip. halve=True dims existing layers before adding.
        self.pending.put((audio, halve))

    def start(self) -> None:
        # Open the stream thread. Call once before feeding audio.
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        # Signal the stream to stop and wait for the thread to exit.
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
            self.thread = None

    def write(self, stream: sd.OutputStream, mono: np.ndarray) -> None:
        # Write a mono chunk to the correct channel of a potentially stereo stream.
        if self.num_channels == 1:
            stream.write(mono.reshape(-1, 1))
        else:
            # Build a stereo frame and write only into self.channel; the other stays 0
            frame = np.zeros((len(mono), self.num_channels), dtype="float32")
            frame[:, self.channel] = mono
            stream.write(frame)

    def loop(self) -> None:
        # Main audio thread: drain pending queue, mix active layers, write chunks.
        layers: list[dict] = []
        try:
            with sd.OutputStream(
                samplerate=SAMPLE_RATE,
                device=self.device,
                channels=self.num_channels,
                dtype="float32",
            ) as stream:
                while not self.stop_event.is_set():
                    # Pull all newly queued clips in one go
                    try:
                        while True:
                            audio, halve = self.pending.get_nowait()
                            if halve:
                                # Dim existing layers and drop any that are now too quiet
                                for layer in layers:
                                    layer["vol"] *= 0.4
                                layers = [l for l in layers if l["vol"] >= self.VOL_THRESHOLD]
                            layers.append({"audio": audio, "pos": 0, "vol": 1.0})
                            if len(layers) > self.MAX_LAYERS:
                                layers = layers[-self.MAX_LAYERS:]
                    except queue.Empty:
                        pass

                    self.has_audio = bool(layers)

                    if not layers:
                        # Write silence to keep the stream alive and prevent underruns
                        self.write(stream, np.zeros(self.CHUNK, dtype="float32"))
                        continue

                    # Mix all active layers; remove any that have finished playing
                    mixed = np.zeros(self.CHUNK, dtype="float32")
                    active = []
                    for layer in layers:
                        audio = layer["audio"]
                        pos = layer["pos"]
                        end = min(pos + self.CHUNK, len(audio))
                        chunk = audio[pos:end]
                        if len(chunk) < self.CHUNK:
                            chunk = np.pad(chunk, (0, self.CHUNK - len(chunk)))
                        mixed += chunk * layer["vol"]
                        if end < len(audio):       # still has samples left
                            layer["pos"] = end
                            active.append(layer)
                        # else: clip finished — drop it (play-once semantics)
                    layers = active

                    # Apply master volume and prevent clipping
                    mixed *= self.master[0]
                    peak = float(np.max(np.abs(mixed)))
                    if peak > 1.0:
                        mixed /= peak
                    self.write(stream, mixed)

        except Exception as e:
            print(f"  [audio/echo speaker ({self.device}ch{self.channel}) error: {e}]")


class BackgroundEchoPlayer:
    """
    Dispatches echo clips across all ECHO_OUTPUTS speakers.

    Prefers an empty speaker so each echo comes from a different position.
    If all speakers are busy, picks one at random and halves its existing layers
    before adding the new clip. master volume is shared with all SpeakerPlayers
    so set_volume() affects all of them instantly.
    """

    def __init__(self, echo_volume: float = 0.65) -> None:
        self.echo_volume = echo_volume  # base gain applied before reverse_reverb
        self.master: list = [1.0]       # shared volume multiplier for all speakers
        self.speakers = [
            SpeakerPlayer(dev, ch, nch, self.master)
            for dev, ch, nch in ECHO_OUTPUTS
        ]

    def feed(self, echo_pcm: bytes) -> None:
        # Process echo PCM and route it to the best available speaker.
        audio = reverse_reverb(process_entity_audio(echo_pcm) * self.echo_volume)
        indices = list(range(len(self.speakers)))
        random.shuffle(indices)
        # Prefer any empty speaker; fall back to a random occupied one
        for i in indices:
            if self.speakers[i].is_empty():
                self.speakers[i].feed(audio, halve=False)
                return
        self.speakers[random.choice(indices)].feed(audio, halve=True)

    def set_volume(self, v: float) -> None:
        # Set master volume (0.0–1.0) for all speakers instantly.
        self.master[0] = max(0.0, min(1.0, v))

    def fade_out(self, duration_secs: float = 20.0) -> None:
        # Smoothly fade all speakers to silence over duration_secs (non-blocking).
        start_vol = self.master[0]
        steps = max(1, int(duration_secs / 0.05))

        def do_fade() -> None:
            for i in range(steps + 1):
                t = i / steps
                self.master[0] = start_vol * float(np.cos(t * np.pi / 2))
                time.sleep(0.05)
            self.master[0] = 0.0

        threading.Thread(target=do_fade, daemon=True).start()

    def start(self) -> None:
        for s in self.speakers:
            s.start()

    def stop(self) -> None:
        for s in self.speakers:
            s.stop()

    def __enter__(self) -> "BackgroundEchoPlayer":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


def play_main_interruptible(main_pcm: bytes, ritual: "ClapRitual") -> None:
    # Play main voice on MAIN_OUTPUT with reverse-reverb swell. Stops on double clap or abort.
    TAIL = np.zeros(int(0.5 * SAMPLE_RATE), dtype="float32")
    raw = np.frombuffer(main_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    processed = reverse_reverb(raw)
    # Additional 2-second cosine fade at the very end for a clean finish
    fade_n = min(int(2.0 * SAMPLE_RATE), len(processed))
    processed[-fade_n:] *= np.cos(np.linspace(0.0, np.pi / 2, fade_n, dtype="float32"))
    audio = np.concatenate([processed, TAIL])
    CHUNK = int(SAMPLE_RATE * 0.05)
    stop = threading.Event()
    done = threading.Event()

    def play() -> None:
        try:
            with sd.OutputStream(
                samplerate=SAMPLE_RATE, device=MAIN_OUTPUT, channels=1, dtype="float32"
            ) as stream:
                pos = 0
                while pos < len(audio) and not stop.is_set():
                    stream.write(audio[pos: pos + CHUNK].reshape(-1, 1))
                    pos += CHUNK
        except Exception as e:
            print(f"  [audio/main stream error: {e}]")
        finally:
            done.set()

    ritual.double.clear()
    t = threading.Thread(target=play, daemon=True)
    t.start()
    # Main thread polls for interruption while audio plays in background
    while not done.is_set():
        if ritual.double.is_set() or ritual.abort.is_set():
            stop.set()
            break
        time.sleep(0.02)
    t.join()


def play_bell(
    bell_path: str,
    *,
    times: int = 1,
    overlap_secs_min: float = 0.0,
    overlap_secs_max: float = 0.0,
    greeting_audio: np.ndarray | None = None,
    greeting_offset_secs: float = 2.0,
    on_bell: "Callable[[int], None] | None" = None,
) -> None:
    """
    Mix and play a bell with optional repetition, greeting overlay, and strike callbacks.

    greeting_audio: reverse-reverb float32 array mixed into MAIN_OUTPUT at greeting_offset_secs.
    on_bell: called with the 1-based bell number at the exact sample position of each strike.
             Used to step echo volume down over farewell bells.
    """
    bell = load_bell(bell_path)
    # Randomise gap between strikes within the given range
    overlap_secs = np.random.uniform(overlap_secs_min, overlap_secs_max)
    step = int(overlap_secs * SAMPLE_RATE)
    total = step * (times - 1) + len(bell)

    offset = 0
    if greeting_audio is not None:
        offset = int(greeting_offset_secs * SAMPLE_RATE)
        total = max(total, offset + len(greeting_audio))

    # Build the full mixed buffer: N bell strikes + optional greeting
    mixed = np.zeros(total, dtype="float32")
    for i in range(times):
        mixed[i * step: i * step + len(bell)] += bell

    if greeting_audio is not None:
        mixed[offset: offset + len(greeting_audio)] += greeting_audio

    peak = float(np.max(np.abs(mixed)))
    if peak > 1.0:
        mixed /= peak

    if on_bell is not None:
        # Chunk-based streaming so on_bell fires at the exact sample of each strike
        CHUNK = int(SAMPLE_RATE * 0.05)
        bell_starts = [i * step for i in range(times)]
        next_bell = 0
        with sd.OutputStream(
            samplerate=SAMPLE_RATE, device=MAIN_OUTPUT, channels=1, dtype="float32"
        ) as stream:
            pos = 0
            while pos < len(mixed):
                # Fire all callbacks whose sample position we've reached
                while next_bell < len(bell_starts) and pos >= bell_starts[next_bell]:
                    on_bell(next_bell + 1)
                    next_bell += 1
                chunk = mixed[pos:pos + CHUNK]
                if len(chunk) < CHUNK:
                    chunk = np.pad(chunk, (0, CHUNK - len(chunk)))
                stream.write(chunk.reshape(-1, 1))
                pos += CHUNK
    else:
        # No callbacks needed — simpler one-shot play
        sd.play(mixed, samplerate=SAMPLE_RATE, device=MAIN_OUTPUT)
        sd.wait()


def record_until_double_clap(ritual: "ClapRitual", filename: str) -> None:
    # Record continuously at PRAYER_SAMPLE_RATE, stopping on double clap. Saves to filename.
    chunks: list[np.ndarray] = []
    q: queue.Queue = queue.Queue()
    CHUNK_SIZE = int(PRAYER_SAMPLE_RATE * 0.05)

    ritual.double.clear()

    def callback(indata, _frames, _t, _status):
        q.put(indata.copy())

    with sd.InputStream(samplerate=PRAYER_SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=CHUNK_SIZE, callback=callback):
        while True:
            if ritual.double.is_set():
                break
            try:
                chunks.append(q.get(timeout=0.1))
            except queue.Empty:
                pass

    if chunks:
        sf.write(filename, np.concatenate(chunks, axis=0), PRAYER_SAMPLE_RATE)


def record_question(ritual: "ClapRitual", filename: str) -> bool:
    """
    Record a question using voice activity detection. Returns True if session-end double clap fired.

    Pipeline:
      1. Calibrate ambient noise level over CAL_CHUNKS (≈1 second).
      2. Wait for SPEECH_ONSET_CHUNKS consecutive loud chunks (prevents false triggers).
      3. Record until SILENCE_CHUNKS consecutive quiet chunks (end of speech).
      4. Save to filename at SAMPLE_RATE.
    paused event discards current recording state so the operator can intervene.
    """
    CHUNK_SIZE = int(SAMPLE_RATE * 0.05)
    SILENCE_CHUNKS = int(1.5 / 0.05)       # 1.5 s of silence ends recording
    SPEECH_ONSET_CHUNKS = 2                 # 2 consecutive loud chunks to start recording
    CAL_CHUNKS = 20                         # calibration window (~1 second)

    ritual.double.clear()

    q: queue.Queue = queue.Queue()
    chunks: list[np.ndarray] = []
    cal_rms: list[float] = []
    speech_started = False
    onset_count = 0
    silence_count = 0
    vad_threshold = 0.001

    def callback(indata, _frames, _t, _status):
        q.put(indata.copy())

    print("  [Listening...]")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=CHUNK_SIZE, callback=callback):
        while True:
            if ritual.double.is_set():
                return True     # participant is ending the session

            try:
                chunk = q.get(timeout=0.1)
            except queue.Empty:
                continue

            # Phase 1: calibrate noise floor
            if len(cal_rms) < CAL_CHUNKS:
                cal_rms.append(float(np.sqrt(np.mean(chunk ** 2))))
                if len(cal_rms) == CAL_CHUNKS:
                    vad_threshold = max(float(np.mean(cal_rms)) * 2, 0.001)
                continue

            # Operator pause: discard everything and restart
            if ritual.paused.is_set():
                speech_started = False
                onset_count = 0
                silence_count = 0
                chunks.clear()
                continue

            if not speech_started:
                # Phase 2: wait for onset (avoids triggering on transient noise)
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
                # Phase 3: record until sustained silence
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
