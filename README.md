# Tuhan Kami

_Tuhan Kami_ — _Tuhan_ is "God" in Malay; _Kami_ is "God" (神) in Japanese. Together as a Malay phrase, _Tuhan Kami_ means "Our God."

This program is a key part of an interactive art installation that reconstructs the bicameral voice: the ancient, authoritative inner voice that psychologist Julian Jaynes theorised was once heard by all humans as the literal word of god, before the emergence of modern consciousness silenced it.

## How It Works

The installation runs as a pseudo-ritual:

1. **Prayer** — The participant speaks a prayer aloud. The Entity learns the seeker's voice and their innermost concerns from what they offer to god.

2. **Transfiguration** — The prayer recording is uploaded to ElevenLabs, cloned, and then remixed: algorithmically reshaped into something deeper, older, and vast. An _"inverted version of oneself."_. Local audio processing then adds pitch shift and reverb. This voice is what becomes the Entity's voice.

3. **Dialogue** — The participant holds the ritual buttons and speaks their question. Whisper transcribes it. Claude (acting as the voice of god, anchored to the prayer) responds: authoritative, oracular, specific, and rooted entirely in what the seeker has already revealed about themselves.

4. **Departure** — The participant give thanks to the Entity or says goodbye. A farewell is spoken, three bells toll, and the voice is deleted. No trace remains.

### Signal flow

```
Participant speaks prayer
       ↓
  [Microphone → audio.py]  (record_until_double_clap)
       ↓
  [stt.py]  Whisper transcribes prayer text
       ↓
  [tts.py]  ElevenLabs clones voice → remixes to Entity timbre
       ↓
  [bell] — session begins
       ↓
  ┌──── Participant holds buttons → speaks question ───┐
  │   [audio.py]  record_push_to_talk                  │
  │   [stt.py]    Whisper → question text + language   │
  │   [llm.py]    Claude Haiku → Entity answer         │
  │   [tts.py]    ElevenLabs synthesises answer PCM    │
  │   [audio.py]  pitch shift −2 semitones + reverb    │
  │   [speakers]  play_Entity_pcm_interruptible        │
  └────────────────────────────────────────────────────┘
       ↓ (farewell phrase detected)
  [llm.py]   farewell response
  [tts.py]   speak farewell
  [bell × 3] — session ends
  [tts.py]   delete_voice — voice clone destroyed
```

## Hardware

| Component                                   | Role                                                                             |
| ------------------------------------------- | -------------------------------------------------------------------------------- |
| Macro pad (currently a 3 key configuration) | Ritual interaction: "clap" to begin prayer, hold to speak                        |
| Bone-conducting headset                     | Captures prayers / questions via the microphone, and delivers the Entity's voice |
| Mac (macOS)                                 | Required for `afplay` bell playback                                              |

### Macro pad key mapping

The three keys output the characters `g`, `o`, `d` on press and `b`, `l`, `c` on release. All three must be held simultaneously to record, and pressed twice in quick succession (double-clap) to start and end the prayer.

## Installation

### Prerequisites

- Python 3.12+
- macOS (for `afplay`)
- A working microphone and audio output
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
```

## Running the Installation

```bash
source .venv/bin/activate
python main.py
```

After each session ends, a new one begins immediately, ready for the next participant.

### Session flow for the operator

1. Start `python main.py` before participants arrive.
2. The terminal will display `Clap twice (press all 3 keys twice) to begin your prayer...`
3. The participant interacts independently from this point.
4. When the session ends (participant says goodbye or uses a farewell phrase), `[Session cleared.]` appears and the loop resets.
5. Press `Ctrl-C` to stop the program.

### Debugging: reusing a recorded prayer

If `prayer.wav` exists in the project directory when the session starts, the program will skip the prayer recording step and use that file directly. This is useful for testing the Entity dialogue without repeating the prayer recording each time.

## Entity Behaviour

The Entity is powered by `claude-haiku-4-5`. Its persona is defined in [prompts.py](prompts.py):

- Speaks as god — not _about_ god, not _for_ god.
- Draws directly on the content of the participant's prayer: their stated fears, desires, and circumstances inform every answer.
- Never offers choices. The Entity speaks the singular truth.
- Responds in the same language as the question (English or Japanese detected automatically).
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

## Farewell Phrases

The session ends when the participant's question contains any of the following:

**English:** `thank`, `thanks`, `end`, `stop`, `done`, `finish`, `exit`, `quit`

**Japanese:** `ありがとうございます`, `ありがとう`, `ありがと`, `終わり`, `おわり`, `やめて`, `終わります`, `終了`

## Credits and AI Disclosure

`bell.mp3` is provided by OtoLogic ([梵鐘04](https://otologic.jp/free/se/temple-bells01.html))

Base code and documentation was written by Anthropic's [Claude](https://claude.ai/)

This program requires the use of AI, specifically [Claude](https://console.anthropic.com) and [ElevenLabs](https://elevenlabs.io) to function properly. As part of our compliance, any stored user data (user audio, user chatbot interactions, etc.) are deleted after every session.
