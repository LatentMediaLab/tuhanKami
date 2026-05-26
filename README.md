# Tuhan Kami

_Tuhan Kami_ — _Tuhan_ is "God" in Malay; _Kami (神)_ is "God" in Japanese. Together as a Malay phrase, _Tuhan Kami_ means "Our God."

This program is a key part of an interactive art installation that reconstructs the bicameral voice: the ancient, authoritative inner voice that psychologist Julian Jaynes theorised was once heard by all humans as the literal word of god, before the emergence of modern consciousness silenced it.

## How It Works

The installation runs as a pseudo-ritual:

1. **Prayer** — The participant speaks a prayer aloud. The Entity learns the seeker's voice and their innermost concerns from what they offer to god. A double clap begins and ends the recording.

2. **Transfiguration** — The prayer recording is uploaded to ElevenLabs, cloned, and then remixed. Transformed into something deeper, older, and vast. An _"inverted version of oneself."_. Local audio processing then adds pitch shift and reverb. This voice is what becomes the Entity's voice.

3. **Arrival** — A bell tolls. The Entity then speaks a greeting drawn directly from the prayer, welcoming the seeker by name of spirit.

4. **Dialogue** — The participant speaks their question; the microphone detects speech automatically and stops recording on silence. Whisper transcribes it. Claude (acting as the voice of god, anchored to the prayer) responds: authoritative, oracular, specific, and rooted entirely in what the seeker has already revealed about themselves.

5. **Departure** — The participant claps twice. A farewell is spoken, three bells toll, and the voice is deleted. No trace remains.

### Signal flow

```
Participant claps twice
       ↓
  [clap.py]  ClapRitual detects double clap
       ↓
  [Microphone → audio.py]  record_until_double_clap (VAD)
       ↓  (participant claps twice to stop)
  [stt.py]  Whisper transcribes prayer text
       ↓
  [tts.py]  ElevenLabs clones voice → remixes to Entity timbre
       ↓
  [llm.py]  Entity greeting generated from prayer
  [bell + greeting]  Bell plays; Entity speaks 4 seconds in
       ↓
  ┌─────── Participant speaks question (auto-detected) ───────┐
  │   [audio.py]  record_question — VAD + double-clap watch   │
  │   [stt.py]    Whisper → question text + language          │
  │   [llm.py]    Claude → Entity answer                      │
  │   [tts.py]    ElevenLabs synthesises answer PCM           │
  │   [audio.py]  pitch shift −2 semitones + reverb           │
  │   [speakers]  play_oracle_pcm_interruptible               │
  └───────────────────────────────────────────────────────────┘
       ↓ (participant claps twice)
  [llm.py]   farewell response
  [tts.py]   speak farewell
  [bell × 3, overlapping] — session ends
  [tts.py]   delete_voice — voice clone destroyed
```

## Hardware

| Component               | Role                                                                             |
| ----------------------- | -------------------------------------------------------------------------------- |
| Bone-conducting headset | Captures prayers / questions via the microphone, and delivers the Entity's voice |
| Mac (macOS)             | Required for audio playback via sounddevice                                      |
| TP-Link Tapo smart plug | Drives fans and lights remotely; toggled on/off at session start and end         |

## Installation

### Prerequisites

- Python 3.12+
- macOS
- PortAudio (`brew install portaudio`)
- A working microphone and audio output
- A TP-Link Tapo smart plug on the same local network
- An [Anthropic API key](https://console.anthropic.com)
- An [ElevenLabs API key](https://elevenlabs.io)

### Setup

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Environment variables

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_anthropic_key_here
ELEVENLABS_API_KEY=your_elevenlabs_key_here
TPLINK_HOST-0=000.000.0.000
TPLINK_USERNAME-0=your_tplink_username
TPLINK_PASSWORD-0=your_tplink_password
```

## Running the Installation

```bash
source .venv/bin/activate
python main.py
```

After each session ends, a new one begins immediately, ready for the next participant.

### Session flow for the operator

1. Start `python main.py` before participants arrive.
2. The terminal will display `Clap twice to begin your prayer...`
3. The participant interacts independently from this point — no hardware other than the microphone is needed.
4. When the session ends (participant claps twice), `[Session cleared.]` appears and the loop resets.
5. Press `Ctrl-C` to stop the program.

### Clap interaction summary

| Action                          | Effect                                    |
| ------------------------------- | ----------------------------------------- |
| Double clap (idle)              | Begins prayer recording                   |
| Double clap (recording prayer)  | Ends prayer recording                     |
| Speak naturally                 | Begins question recording (auto-detected) |
| Silence                         | Ends question recording (auto-detected)   |
| Double clap (between questions) | Ends session, triggers farewell           |

### Debugging: reusing a recorded prayer

If `debug.wav` exists in the project directory when the session starts, the program will skip the prayer recording step and use that file directly. This is useful for testing the Entity dialogue without repeating the prayer recording each time.

## Entity Behaviour

The Entity is powered by `claude-haiku-4-5`. Its persona is defined in [prompts.py](prompts.py):

- The Entity speaks as a deity.
- Draws directly on the content of the participant's prayer: their stated fears, desires, and circumstances inform every answer.
- Never offers choices. The Entity speaks the singular truth.
- Responds in the same language as the participant's question (currently only supports English and Japanese).
- Response length scales with question length: 2–3 sentences, capped at ~90 words.
- When a situation seems hopeless, the Entity may give prophecy.

The underlying principle, encoded explicitly in the system prompt, is Jaynes': the voice of god is a reflection of the seeker's own mind.

## Audio Processing

Entity responses receive two local effects applied before playback (`audio.py`):

| Effect            | Parameters                       | Purpose                         |
| ----------------- | -------------------------------- | ------------------------------- |
| Pitch shift       | −2 semitones                     | Deepens and distances the voice |
| Delay-line reverb | 50ms delay, 0.38 decay, 5 echoes | Adds cavern/temple resonance    |

ElevenLabs applies additional shaping during the remix step, guided by this description:

> _"A powerful, ancient, and slightly ominous voice. Deep and resonant, carrying the weight of millennia and the authority of a deity-like entity. Otherworldly and commanding... an inverted version of itself. Majestic but also chilling."_

### Bell sounds

Bell audio is drawn randomly from four variations of `bonsho/Bonsho04-*.mp3` each session. On arrival, the bell plays once with the Entity's greeting overlaid 3 seconds in. On departure, the bell plays three times with each strike overlapping the previous by 7 seconds.

## Credits and AI Disclosure

Bell audio provided by OtoLogic ([梵鐘04](https://otologic.jp/free/se/temple-bells01.html))

Base code and documentation was written by Anthropic's [Claude](https://claude.ai/)

This program requires the use of AI, specifically [Claude](https://console.anthropic.com) and [ElevenLabs](https://elevenlabs.io) to function properly. As part of our compliance, any stored user data (user audio, user chatbot interactions, etc.) are deleted after every session.
