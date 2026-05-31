import asyncio
import os
import random
import select
import sys
import termios
import threading
import tty
import anthropic
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from kasa import Discover
from audio import (
    record_until_double_clap,
    record_question,
    play_entity_pcm_interruptible,
    play_bell,
    is_silent,
)
from clap import ClapRitual
from stt import transcribe
from llm import ask_entity
from tts import clone_voice, speak, delete_voice

load_dotenv("venv/venv")


def entity_travel(on: bool, device: int) -> None:
    async def _run():
        dev = await Discover.discover_single(
            os.environ["TPLINK_HOST-" + str(device)],
            username=os.environ["TPLINK_USERNAME"],
            password=os.environ["TPLINK_PASSWORD"],
        )
        if on:
            await dev.turn_on()
        else:
            await dev.turn_off()
        await dev.update()
        await dev.disconnect()

    asyncio.run(_run())


DEBUG_WAV = "debug.wav"
PRAYER_WAV = "prayer.wav"
QUESTION_WAV = "question.wav"
BELL_WAV1 = "bonsho/Bonsho04-1.mp3"
BELL_WAV2 = "bonsho/Bonsho04-2.mp3"
BELL_WAV3 = "bonsho/Bonsho04-3.mp3"
BELL_WAV4 = "bonsho/Bonsho04-4.mp3"


class RitualAborted(Exception):
    pass


def _check(abort: threading.Event) -> None:
    if abort.is_set():
        raise RitualAborted


def run_ritual(
    anthropic_client: anthropic.Anthropic,
    eleven_client: ElevenLabs,
    ritual: ClapRitual,
) -> None:
    abort = ritual.abort
    prayer_audio = PRAYER_WAV
    bell_audio = random.choice([BELL_WAV1, BELL_WAV2, BELL_WAV3, BELL_WAV4])

    voice_id = None
    try:
        entity_travel(False, 1)
        entity_travel(True, 2)

        # ── PRAYER ────────────────────────────────────────────────────────────────
        if os.path.exists(DEBUG_WAV):
            print("\n  [Debug mode enabled: using existing recording in debug.wav]")
            prayer_audio = DEBUG_WAV
        else:
            print("\nClap twice to begin your prayer...")
            if not ritual.wait_for_double():
                return
            print("  [Prayer recording started. Clap twice to finish.]\n")
            record_until_double_clap(ritual, PRAYER_WAV)
            _check(abort)

        print("  [Transcribing...]")
        entity_travel(True, 1)
        prayer_text, _ = transcribe(prayer_audio)
        _check(abort)
        print("  Prayer received.\n")

        # ── VOICE CLONE + GREETING ────────────────────────────────────────────────
        voice_id = clone_voice(eleven_client, prayer_audio)
        _check(abort)
        entity_travel(False, 1)
        greeting = ask_entity(anthropic_client, [], "Offer a brief, mystical greeting to the seeker who has just arrived. It should be related to the prayer they just shared. Again, it should be brief and welcoming, like something an entity or spirit might say to acknowledge the seeker's presence and prayer.", prayer_text)
        _check(abort)
        greeting_pcm = speak(eleven_client, voice_id, greeting)
        _check(abort)
        print(f"\n  They: {greeting}\n")
        entity_travel(False, 2)
        play_bell(bell_audio, greeting_pcm=greeting_pcm, greeting_offset_secs=3.0)
        _check(abort)
        print("\nSpeak your question, or clap twice to end the ritual.\n")

        messages = []

        while True:
            ended = record_question(ritual, QUESTION_WAV)
            _check(abort)

            if ended:
                print("  [They depart...]")
                farewell = ask_entity(
                    anthropic_client, messages, "The seeker is leaving. Offer a brief farewell.", prayer_text
                )
                _check(abort)
                print(f"\n  They: {farewell}\n")
                entity_travel(True, 1)
                pcm = speak(eleven_client, voice_id, farewell)
                _check(abort)
                play_entity_pcm_interruptible(pcm, ritual)
                entity_travel(False, 1)
                play_bell(bell_audio, times=3, overlap_secs_min=5.5, overlap_secs_max=7.0)
                entity_travel(True, 2)
                break

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
            print("  [They speak...]")
            answer = ask_entity(
                anthropic_client, messages, question, prayer_text, language=q_lang
            )
            _check(abort)
            print(f"\n  They: {answer}\n")

            pcm = speak(eleven_client, voice_id, answer)
            _check(abort)
            play_entity_pcm_interruptible(pcm, ritual)
            _check(abort)

    except RitualAborted:
        pass
    finally:
        if voice_id is not None:
            delete_voice(eleven_client, voice_id)
        for path in [PRAYER_WAV, QUESTION_WAV]:
            if os.path.exists(path):
                os.remove(path)
        print("  [Ritual cleared.]\n")


def _read_key() -> str | None:
    """Return a single keypress if one is available, else None."""
    if select.select([sys.stdin], [], [], 0.1)[0]:
        return sys.stdin.read(1)
    return None


def main() -> None:
    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    eleven_client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)

        while True:
            print("\nPress 'o' to begin the ritual...\n")

            while True:
                key = _read_key()
                if key == 'o':
                    break

            print("  [Ritual unlocked.]\n")

            with ClapRitual() as ritual:
                ritual_thread = threading.Thread(
                    target=run_ritual,
                    args=(anthropic_client, eleven_client, ritual),
                    daemon=True,
                )
                ritual_thread.start()

                while ritual_thread.is_alive():
                    key = _read_key()
                    if key in ('i', 'p'):
                        print("\n  [Ritual interrupted. Restarting...]\n")
                        ritual.abort.set()
                        ritual._double.set()  # unblock any audio loop waiting on double
                        ritual_thread.join()
                        break

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


if __name__ == "__main__":
    main()
