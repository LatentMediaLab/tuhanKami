import os
import re

import anthropic
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

from audio import (
    record_push_to_talk,
    record_until_double_clap,
    play_oracle_pcm_interruptible,
    play_bell,
    is_silent,
)
from buttons import ButtonState, ClapDetector, button_mode, read_char
from stt import transcribe
from llm import ask_oracle
from tts import clone_voice, speak, delete_voice

load_dotenv()

DEBUG_WAV = "debug.wav"
PRAYER_WAV = "prayer.wav"
QUESTION_WAV = "question.wav"

GOODBYE_WORDS_EN = {"thank", "thanks", "end", "stop", "done", "finish", "exit", "quit"}
GOODBYE_WORDS_JA = {"ありがとうございます", "ありがとう", "ありがと", "終わり", "おわり", "やめて", "終わります", "終了"}


def should_end_session(question: str) -> bool:
    words = set(re.findall(r"\b[a-z']+\b", question.lower()))
    if words & GOODBYE_WORDS_EN:
        return True
    return any(phrase in question for phrase in GOODBYE_WORDS_JA)


def _wait_for_release(state: ButtonState) -> None:
    """Block until all macro keys are released."""
    while state.all_held:
        ch = read_char(timeout=0.02)
        if ch:
            state.update(ch)


def _wait_for_press(state: ButtonState) -> None:
    """Block until all macro keys are held."""
    while not state.all_held:
        ch = read_char(timeout=0.02)
        if ch:
            state.update(ch)


def _wait_for_double_clap(state: ButtonState) -> None:
    """Block until a double-clap is detected."""
    detector = ClapDetector()
    while not detector.update(state):
        ch = read_char(timeout=0.02)
        if ch:
            state.update(ch)


def run_session(
    anthropic_client: anthropic.Anthropic,
    eleven_client: ElevenLabs,
    state: ButtonState,
) -> None:

    # ── PRAYER ────────────────────────────────────────────────────────────────
    if os.path.exists(DEBUG_WAV):
        print("\n  [Debug mode enabled: using existing recording in debug.wav]")
    else:
        print("\nClap twice (press all 3 keys twice) to begin your prayer...")
        _wait_for_double_clap(state)
        print("  [Prayer recording started. Clap twice to finish.]\n")
        record_until_double_clap(state, PRAYER_WAV)

    print("  [Transcribing...]")
    prayer_text, _ = transcribe(PRAYER_WAV)
    print("  Prayer received.\n")

    # ── VOICE CLONE ───────────────────────────────────────────────────────────
    voice_id = clone_voice(eleven_client, PRAYER_WAV)
    play_bell("bell.mp3")
    print("\nHold all 3 keys while speaking. Release to send.\n")

    messages = []

    try:
        while True:
            print("Hold all 3 keys to ask your question...")

            # Ensure buttons are released before we start watching for a new press
            _wait_for_release(state)
            # Now wait for the user to press all 3 to start recording
            _wait_for_press(state)

            record_push_to_talk(state, QUESTION_WAV)

            if not os.path.exists(QUESTION_WAV) or is_silent(QUESTION_WAV):
                print("  (nothing captured — try again)\n")
                if os.path.exists(QUESTION_WAV):
                    os.remove(QUESTION_WAV)
                continue

            question, q_lang = transcribe(QUESTION_WAV)
            os.remove(QUESTION_WAV)

            if not question:
                continue

            print(f"  You: {question}")

            if should_end_session(question):
                print("  [They depart...]")
                farewell = ask_oracle(
                    anthropic_client, messages, "The seeker is leaving. Offer a brief farewell.", prayer_text, language=q_lang
                )
                print(f"\n  They: {farewell}\n")
                pcm = speak(eleven_client, voice_id, farewell)
                play_oracle_pcm_interruptible(pcm, state)
                play_bell("bell.mp3", times=3)
                break

            print("  [They speak...]")
            answer = ask_oracle(
                anthropic_client, messages, question, prayer_text, language=q_lang
            )
            print(f"\n  They: {answer}\n")

            pcm = speak(eleven_client, voice_id, answer)
            play_oracle_pcm_interruptible(pcm, state)

            # If playback was interrupted by button press, wait for release
            # so the next loop doesn't immediately start recording
            _wait_for_release(state)

    finally:
        delete_voice(eleven_client, voice_id)
        for path in [PRAYER_WAV, QUESTION_WAV]:
            if os.path.exists(path):
                os.remove(path)
        print("  [Session cleared.]\n")


def main() -> None:
    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    eleven_client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    while True:
        state = ButtonState()
        with button_mode():
            run_session(anthropic_client, eleven_client, state)


if __name__ == "__main__":
    main()
