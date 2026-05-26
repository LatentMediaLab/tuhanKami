import asyncio
import os
import random
import anthropic
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from kasa import Discover
from audio import (
    record_until_double_clap,
    record_question,
    play_oracle_pcm_interruptible,
    play_bell,
    is_silent,
)
from clap import ClapSession
from stt import transcribe
from llm import ask_oracle
from tts import clone_voice, speak, delete_voice

load_dotenv()


def entity_travel(on: bool) -> None:
    async def _run():
        dev = await Discover.discover_single(
            os.environ["TPLINK_HOST"],
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


def run_session(
    anthropic_client: anthropic.Anthropic,
    eleven_client: ElevenLabs,
    session: ClapSession,
) -> None:
    prayer_audio = PRAYER_WAV
    bell_audio = random.choice([BELL_WAV1, BELL_WAV2, BELL_WAV3, BELL_WAV4])

    # ── PRAYER ────────────────────────────────────────────────────────────────
    if os.path.exists(DEBUG_WAV):
        print("\n  [Debug mode enabled: using existing recording in debug.wav]")
        prayer_audio = DEBUG_WAV
    else:
        print("\nClap twice to begin your prayer...")
        session.wait_for_double()
        print("  [Prayer recording started. Clap twice to finish.]\n")
        record_until_double_clap(session, PRAYER_WAV)

    print("  [Transcribing...]")
    prayer_text, _ = transcribe(prayer_audio)
    print("  Prayer received.\n")

    # ── VOICE CLONE + GREETING ────────────────────────────────────────────────
    entity_travel(True)
    voice_id = clone_voice(eleven_client, prayer_audio)
    entity_travel(False)
    greeting = ask_oracle(anthropic_client, [], "Offer a brief, mystical greeting to the seeker who has just arrived. It should be related to the prayer they just shared. Again, it should be brief and welcoming, like something an oracle or spirit might say to acknowledge the seeker's presence and prayer.", prayer_text)
    greeting_pcm = speak(eleven_client, voice_id, greeting)
    print(f"\n  They: {greeting}\n")
    play_bell(bell_audio, greeting_pcm=greeting_pcm, greeting_offset_secs=3.0)
    print("\nSpeak your question, or clap twice to end the session.\n")

    messages = []

    try:
        while True:
            ended = record_question(session, QUESTION_WAV)

            if ended:
                print("  [They depart...]")
                farewell = ask_oracle(
                    anthropic_client, messages, "The seeker is leaving. Offer a brief farewell.", prayer_text
                )
                print(f"\n  They: {farewell}\n")
                entity_travel(True)
                pcm = speak(eleven_client, voice_id, farewell)
                play_oracle_pcm_interruptible(pcm, session)
                entity_travel(False)
                play_bell(bell_audio, times=3, overlap_secs=7.0)
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
            answer = ask_oracle(
                anthropic_client, messages, question, prayer_text, language=q_lang
            )
            print(f"\n  They: {answer}\n")

            pcm = speak(eleven_client, voice_id, answer)
            play_oracle_pcm_interruptible(pcm, session)

    finally:
        entity_travel(False)
        delete_voice(eleven_client, voice_id)
        for path in [PRAYER_WAV, QUESTION_WAV]:
            if os.path.exists(path):
                os.remove(path)
        print("  [Session cleared.]\n")


def main() -> None:
    anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    eleven_client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

    while True:
        with ClapSession() as session:
            run_session(anthropic_client, eleven_client, session)


if __name__ == "__main__":
    main()
