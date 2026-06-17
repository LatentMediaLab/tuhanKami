# Tuhan Kami

_Tuhan Kami_ — _Tuhan_ is "God" in Malay; _Kami (神)_ is "God" in Japanese. Together as a Malay phrase, _Tuhan Kami_ means "Our God."

This program is a key part of an interactive art installation that reconstructs the bicameral voice: the ancient, authoritative inner voice that psychologist Julian Jaynes theorised was once heard by all humans as the literal word of god, before the emergence of modern consciousness silenced it.

## How It Works

The installation runs as a pseudo-ritual:

1. **Prayer** — The participant speaks a prayer aloud. The Entity learns the seeker's voice and their innermost concerns from what they offer to god. A double clap begins and ends the recording.

2. **Transfiguration** — The prayer recording is uploaded to ElevenLabs, cloned twice, and one clone is remixed. Transformed into something deeper, older, and vast. An _"inverted version of oneself."_ This voice becomes the Entity's echo. The plain clone becomes the intelligible divine voice.

3. **Arrival** — A bell tolls. The Entity speaks a greeting drawn directly from the prayer, overlaid on the bell 3 seconds in.

4. **Dialogue** — The participant speaks their question; the microphone detects speech automatically and stops recording on silence. Whisper transcribes it. Claude (acting as the voice of god, anchored to the prayer) responds: authoritative, oracular, specific, and rooted entirely in what the seeker has already revealed about themselves. Each answer is spoken twice simultaneously — once through the headset (clear, divine) and once through surrounding speakers (fragmented, animalistic echo).

5. **Departure** — The participant claps twice. A farewell is spoken, three bells toll with each strike stepping the echo volume down to silence, and both voice clones are deleted. No trace remains.

### Signal flow

```
Participant claps twice
       ↓
  [clap.py]  ClapRitual detects double clap
       ↓
  [audio.py]  record_until_double_clap — records at 44100 Hz until next double clap
       ↓  (participant claps twice to stop)
  [stt.py]  transcribe — Whisper transcribes prayer text
       ↓
  [tts.py]  clone_voices — two ElevenLabs IVC clones in parallel
            clone 1: plain IVC  →  main voice (headset)
            clone 2: IVC + remix →  echo voice (surrounding speakers)
       ↓
  [llm.py]  ask_entity — Entity greeting generated from prayer
  [audio.py] play_bell — bell plays; Entity speaks 3 seconds in
       ↓
  ┌─────── Participant speaks question (auto-detected) ───────────────────────┐
  │   [audio.py]  record_question — VAD calibrates noise, detects onset,     │
  │               records until 1.5 s silence; double clap exits loop        │
  │   [stt.py]    transcribe — question text + language                      │
  │   [llm.py]    ask_entity — Entity answer (scales with question length)   │
  │   [llm.py]    ask_echo   — fragmented echo text (~15% word count)        │
  │   [tts.py]    speak ×2   — main PCM + echo PCM (main + echo in parallel) │
  │   [audio.py]  process_entity_audio — pitch shift −2st + reverb           │
  │   [audio.py]  reverse_reverb — swell-in / fade-out effect                │
  │   [audio.py]  play_main_interruptible — headset; stoppable by clap       │
  │   [audio.py]  BackgroundEchoPlayer.feed — routed to a free echo speaker  │
  └───────────────────────────────────────────────────────────────────────────┘
       ↓ (participant claps twice)
  [llm.py]   ask_entity — farewell
  [tts.py]   speak ×2 — farewell main + echo PCM
  [audio.py] play_bell ×3 — overlapping bells; on_bell steps echo to 0
  [tts.py]   delete_voices — both voice clones destroyed
```

## File Overview

| File | Purpose |
| ---- | ------- |
| `main.py` | Operator event loop, ritual orchestration, TP-Link smart plug control |
| `audio.py` | All audio processing: pitch shift, reverb, recording, playback, echo routing |
| `clap.py` | Background double-clap detector using a bandpass filter + peak detection |
| `stt.py` | Whisper transcription wrapper |
| `llm.py` | Claude API calls: Entity response + echo text generation |
| `tts.py` | ElevenLabs: voice cloning, synthesis, deletion |
| `prompts.py` | Entity system prompt template (the Entity's persona and rules) |

## Hardware

| Component               | Role                                                                             |
| ----------------------- | -------------------------------------------------------------------------------- |
| Bone-conducting headset | Captures prayers / questions via the microphone, and delivers the Entity's voice |
| Mac (macOS)             | Required for audio playback via sounddevice / PortAudio                          |
| Surrounding speakers    | Echo voice — routed via `ECHO_OUTPUTS` in `audio.py`                            |
| TP-Link Tapo smart plug | Drives fans and lights remotely; toggled on/off at session start and end         |

## Installation

### Prerequisites

- Python 3.12+
- macOS
- PortAudio (`brew install portaudio`)
- FFmpeg (`brew install ffmpeg`)
- A working microphone and audio output
- A TP-Link Tapo smart plug on the same local network
- An [Anthropic API key](https://console.anthropic.com)
- An [ElevenLabs API key](https://elevenlabs.io)

### Setup

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Environment variables

Create a file at `venv/venv`:

```
ANTHROPIC_API_KEY=your_anthropic_key_here
ELEVENLABS_API_KEY=your_elevenlabs_key_here
TPLINK_HOST-1=000.000.0.000
TPLINK_HOST-2=000.000.0.000
TPLINK_USERNAME=your_tplink_username
TPLINK_PASSWORD=your_tplink_password
```

### Audio device configuration

Open `audio.py` and update these two constants to match your audio setup:

```python
MAIN_OUTPUT = 3   # sounddevice device index for the headset / main speaker
ECHO_OUTPUTS = [
    (5, 0, 2),  # (device_index, channel_index, total_channels) for each echo speaker
    (5, 1, 2),
]
```

Run `python -c "import sounddevice as sd; print(sd.query_devices())"` to list available device indices.

For multiple independent speakers on separate stereo devices, add one tuple per channel:
```python
ECHO_OUTPUTS = [(5,0,2),(5,1,2),(6,0,2),(6,1,2),(7,0,2),(7,1,2)]
```

## Running the Installation

```bash
source .venv/bin/activate
python3 main.py
```

After each session ends, the program returns to the idle state and waits for the operator to press `o` again before the next participant can begin.

### Session flow for the operator

1. Start `python main.py` before participants arrive.
2. The terminal will display `Press 'o' to begin the ritual...`
3. When the next participant is ready, press **`o`** on the keyboard. The terminal will display `Clap twice to begin your prayer...`
4. The participant interacts independently from this point — no hardware other than the microphone is needed.
5. When the session ends (participant claps twice), `[Ritual cleared.]` appears and the program returns to step 2.
6. If a false positive or interruption occurs at any point, press **`i`** or **`p`** to immediately reset the ritual back to step 2.
7. Press `Ctrl-C` to stop the program.

### Keyboard controls

| Key      | Effect                                                                 |
| -------- | ---------------------------------------------------------------------- |
| `o`      | Unlocks the ritual — allows clap detection to begin                    |
| `o` hold | Pauses question recording (discards current take); release to resume   |
| `i`/`p`  | Interrupts and resets the ritual at any stage, returning to idle state |
| `Ctrl-C` | Exits the program                                                      |

### Clap interaction summary

| Action                          | Effect                                    |
| ------------------------------- | ----------------------------------------- |
| Double clap (idle)              | Begins prayer recording                   |
| Double clap (recording prayer)  | Ends prayer recording                     |
| Speak naturally                 | Begins question recording (auto-detected) |
| Silence (1.5 s)                 | Ends question recording (auto-detected)   |
| Double clap (between questions) | Ends session, triggers farewell           |

### Debugging: reusing a recorded prayer

If `debug.wav` exists in the project directory when the session starts, the program will skip the prayer recording step and use that file directly. Useful for testing the Entity dialogue without repeating the prayer recording each time.


## Entity Behaviour

The Entity is powered by `claude-haiku-4-5`. Its persona is defined in [prompts.py](prompts.py):

- The Entity speaks as a deity.
- Draws directly on the content of the participant's prayer: their stated fears, desires, and circumstances inform every answer.
- Never offers choices. The Entity speaks the singular truth.
- Responds in the same language as the participant's question (currently supports English and Japanese).
- Response length scales with question length: 2–3 sentences, capped at ~90 words.
- When a situation seems hopeless, the Entity may give prophecy.

The underlying principle, encoded explicitly in the system prompt, is Jaynes': the voice of god is a reflection of the seeker's own mind.

## Audio Processing

### Main voice (headset)

The main voice PCM from ElevenLabs is processed by `reverse_reverb` before playback:

1. Trailing TTS silence is trimmed so the fade anchors to real speech content.
2. The audio is reversed, heavy reverb is applied (which bleeds energy backward in time), then reversed again — reverb energy now builds up *into* the speech (swell-in effect).
3. A cosine fade begins 3 seconds before the speech ends and continues through a 1.5-second silence post-pad, creating a natural trailing decay.

### Echo voice (surrounding speakers)

Each echo clip is additionally processed by `process_entity_audio` before `reverse_reverb`:

| Effect      | Parameters                       | Purpose                         |
| ----------- | -------------------------------- | ------------------------------- |
| Pitch shift | −2 semitones                     | Deepens and distances the voice |
| Reverb      | 50ms delay, 0.38 decay, 5 echoes | Adds cavern/temple resonance    |

The echo is then routed to a free echo speaker by `BackgroundEchoPlayer`. If all speakers are occupied, an existing one is chosen at random, its layers are dimmed to 40%, and the new clip is layered on top.

### ElevenLabs voice shaping

The echo clone is additionally shaped by ElevenLabs remix during voice creation, guided by this description:

> _"A powerful, ancient, and slightly ominous voice. Deep and resonant, carrying the weight of millennia and the authority of a deity-like entity. Otherworldly and commanding... an inverted version of itself. Majestic but also chilling."_

### Bell sounds

Bell audio is drawn randomly from four variations of `bonsho/Bonsho04-*.mp3` each session. On arrival, the bell plays once with the Entity's greeting overlaid 3 seconds in. On departure, the bell plays three times with each strike overlapping the previous by a random 5.5–7 seconds; `on_bell` callbacks step echo volume to 60%, 30%, then 0%.

## Credits and AI Disclosure

Bell audio provided by OtoLogic ([梵鐘04](https://otologic.jp/free/se/temple-bells01.html))

Base code and documentation was written by Anthropic's [Claude](https://claude.ai/)

This program requires the use of AI, specifically [Claude](https://console.anthropic.com) and [ElevenLabs](https://elevenlabs.io) to function properly. As part of our compliance, any stored user data (user audio, user chatbot interactions, etc.) are deleted after every session.
