import asyncio
import os
import random
import select
import sys
import termios
import threading
import time
import tty
from concurrent.futures import ThreadPoolExecutor

import anthropic
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from kasa import Discover
from audio import (
    record_until_double_clap,
    record_question,
    play_main_interruptible,
    build_main_audio,
    play_bell,
    is_silent,
    compress_for_ivc,
    BackgroundEchoPlayer,
)
from clap import ClapRitual
from stt import transcribe
from llm import ask_entity, ask_echo
from tts import clone_voices, speak, delete_voices, MAIN_VOICE_SETTINGS, ECHO_VOICE_SETTINGS

load_dotenv("venv/venv")


def entity_travel(on: bool, device: int) -> None:
    # Toggle a TP-Link Tapo smart plug on or off. device is the plug number (1 or 2).
    async def run():
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

    asyncio.run(run())


# File paths used during a ritual session
DEBUG_WAV = "debug.wav"       # if present, skips prayer recording and uses this file instead
PRAYER_WAV = "prayer.wav"     # recorded prayer audio
QUESTION_WAV = "question.wav" # recorded question audio
BELL_WAV1 = "bonsho/Bonsho04-1.mp3"
BELL_WAV2 = "bonsho/Bonsho04-2.mp3"
BELL_WAV3 = "bonsho/Bonsho04-3.mp3"
BELL_WAV4 = "bonsho/Bonsho04-4.mp3"


class RitualAborted(Exception):
    # Raised to cleanly exit the ritual when the operator presses i/p.
    pass


def check(abort: threading.Event) -> None:
    # Raise RitualAborted immediately if the operator has signalled an interrupt.
    if abort.is_set():
        raise RitualAborted


def dual_speak(
    anthropic_client: anthropic.Anthropic,
    eleven_client: ElevenLabs,
    voice_id_main: str,
    voice_id_echo: str,
    answer: str,
    prayer_text: str,
    abort: threading.Event,
) -> tuple[bytes, bytes]:
    """
    Generate main TTS and echo text in parallel, then synthesise echo TTS. Returns (main_pcm, echo_pcm).

    Parallelises the two slowest independent calls (ElevenLabs TTS + Anthropic echo text),
    then does the dependent echo TTS call sequentially after both finish.
    """
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_echo_text = ex.submit(ask_echo, anthropic_client, answer, prayer_text)
        f_main_pcm = ex.submit(speak, eleven_client, voice_id_main, answer, MAIN_VOICE_SETTINGS)
        echo_text = f_echo_text.result()
        print(f"  Echo: {echo_text}")
        check(abort)
        main_pcm = f_main_pcm.result()
        check(abort)
    # Echo TTS must run after echo_text is known
    echo_pcm = speak(eleven_client, voice_id_echo, echo_text, ECHO_VOICE_SETTINGS)
    check(abort)
    return main_pcm, echo_pcm


def run_ritual(
    anthropic_client: anthropic.Anthropic,
    eleven_client: ElevenLabs,
    ritual: ClapRitual,
) -> None:
    """
    Full ritual flow from prayer recording through farewell bells.

    Stages:
      1. Prayer — record or load debug.wav
      2. Transcription — Whisper → prayer_text
      3. Voice cloning — two ElevenLabs IVC voices (plain + remixed)
      4. Greeting — entity speaks a welcome tied to the prayer
      5. Dialogue loop — question → entity answer → echo, repeated until double clap
      6. Farewell — final answer, three overlapping bells stepping echo volume to 0
      7. Cleanup — delete voice clones, remove temp files
    """
    abort = ritual.abort
    prayer_audio = PRAYER_WAV
    bell_audio = random.choice([BELL_WAV1, BELL_WAV2, BELL_WAV3, BELL_WAV4])

    voice_id_main = None
    voice_id_echo = None
    echo_player: BackgroundEchoPlayer | None = None
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
            check(abort)

        print("  [Transcribing...]")
        entity_travel(True, 1)
        prayer_text, prayer_lang = transcribe(prayer_audio)
        check(abort)
        print("  Prayer received.\n")

        # ── VOICE CLONES ──────────────────────────────────────────────────────────
        print("  [Cloning voices...]")
        PRAYER_UPLOAD_WAV = "prayer_upload.wav"
        compress_for_ivc(prayer_audio, PRAYER_UPLOAD_WAV)
        voice_id_main, voice_id_echo = clone_voices(eleven_client, PRAYER_UPLOAD_WAV)
        if os.path.exists(PRAYER_UPLOAD_WAV):
            os.remove(PRAYER_UPLOAD_WAV)
        check(abort)

        echo_player = BackgroundEchoPlayer()
        echo_player.start()

        # ── GREETING ─────────────────────────────────────────────────────────────
        entity_travel(False, 1)
        greeting = ask_entity(anthropic_client, [], "Offer a brief, mystical greeting to the seeker who has just arrived. It should be related to the prayer they just shared. Again, it should be brief and welcoming, like something an entity or spirit might say to acknowledge the seeker's presence and prayer.", prayer_text, language=prayer_lang)
        check(abort)
        print(f"\n  They: {greeting}\n")

        greeting_main_pcm, greeting_echo_pcm = dual_speak(
            anthropic_client, eleven_client, voice_id_main, voice_id_echo,
            greeting, prayer_text, abort,
        )
        echo_player.feed(greeting_echo_pcm)
        # Convert PCM to float32 array for mixing into the bell waveform
        greeting_main_audio = build_main_audio(greeting_main_pcm)

        entity_travel(False, 2)
        # Bell plays once; greeting voice overlaid 3 seconds in
        play_bell(bell_audio, greeting_audio=greeting_main_audio, greeting_offset_secs=3.0)
        check(abort)
        print("\nSpeak your question, or clap twice to end the ritual.\n")

        messages = []  # running conversation history passed to ask_entity each turn

        # ── DIALOGUE LOOP ─────────────────────────────────────────────────────────
        while True:
            ended = record_question(ritual, QUESTION_WAV)
            check(abort)

            if ended:
                # Participant signalled end — generate and speak farewell
                print("  [They depart...]")
                farewell = ask_entity(
                    anthropic_client, messages, "The seeker is leaving. Offer a brief farewell.", prayer_text, language=prayer_lang
                )
                check(abort)
                print(f"\n  They: {farewell}\n")
                entity_travel(True, 1)
                farewell_main_pcm, farewell_echo_pcm = dual_speak(
                    anthropic_client, eleven_client, voice_id_main, voice_id_echo,
                    farewell, prayer_text, abort,
                )
                check(abort)
                echo_player.feed(farewell_echo_pcm)
                play_main_interruptible(farewell_main_pcm, ritual)
                entity_travel(False, 1)

                # Three overlapping bells; on_bell steps echo volume: 60% → 30% → 0%
                bell_vols = {1: 0.6, 2: 0.3, 3: 0.0}

                def on_bell(n: int) -> None:
                    if echo_player is not None:
                        echo_player.set_volume(bell_vols.get(n, 0.0))

                play_bell(bell_audio, times=3, overlap_secs_min=5.5, overlap_secs_max=7.0, on_bell=on_bell)
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
            check(abort)
            print(f"\n  They: {answer}\n")

            main_pcm, echo_pcm = dual_speak(
                anthropic_client, eleven_client, voice_id_main, voice_id_echo,
                answer, prayer_text, abort,
            )
            echo_player.feed(echo_pcm)
            play_main_interruptible(main_pcm, ritual)
            check(abort)

    except RitualAborted:
        pass
    finally:
        # Always clean up — voices and temp files are deleted even if interrupted
        if echo_player is not None:
            echo_player.stop()
        ids = [v for v in (voice_id_main, voice_id_echo) if v is not None]
        if ids:
            delete_voices(eleven_client, *ids)
        for path in [PRAYER_WAV, QUESTION_WAV]:
            if os.path.exists(path):
                os.remove(path)
        print("  [Ritual cleared.]\n")


def read_key() -> str | None:
    # Non-blocking single-keypress read. Returns the character or None if nothing was pressed.
    if select.select([sys.stdin], [], [], 0.1)[0]:
        return sys.stdin.read(1)
    return None


def main() -> None:
    # Operator event loop: waits for 'o' to unlock, runs a ritual, repeats.
    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    eleven_client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        # Raw mode so key presses are read immediately without waiting for Enter
        tty.setcbreak(fd)

        while True:
            print("\nPress 'o' to begin the ritual...\n")

            while True:
                key = read_key()
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

                last_o = 0.0
                while ritual_thread.is_alive():
                    key = read_key()
                    if key in ('i', 'p'):
                        # Interrupt: signal abort, unblock any clap wait, clear pause
                        print("\n  [Ritual interrupted. Restarting...]\n")
                        ritual.abort.set()
                        ritual.double.set()
                        ritual.paused.clear()
                        ritual_thread.join()
                        break
                    elif key == 'o':
                        # Hold 'o' to pause recording; release (no 'o' for >0.15s) to resume
                        if not ritual.paused.is_set():
                            print("\n  [Recording paused]\n")
                            ritual.paused.set()
                        last_o = time.monotonic()

                    if ritual.paused.is_set() and time.monotonic() - last_o > 0.15:
                        ritual.paused.clear()
                        print("\n  [Recording resumed]\n")

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


if __name__ == "__main__":
    main()
