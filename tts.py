from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

MODEL_ID = "eleven_flash_v2_5"
OUTPUT_FORMAT = "pcm_16000"

ORACLE_DESCRIPTION = (
    "A powerful, ancient, and slightly ominous voice. Deep and resonant, carrying the weight of millennia and the authority of a deity-like entity. Otherworldly and commanding, evoking both awe and reverence. Make the voice much louder than the original recording, as if the entity is speaking from a vast cavern or temple. Make the voice so that it is still similar to the original cloned voice, but somehow like an inverted version of itself. A voice that is majestic but also chilling/scary"
)

ORACLE_VOICE_SETTINGS = VoiceSettings(
    stability=0.40,
    similarity_boost=0.95,
    style=0.60,
    speed=0.90,
    use_speaker_boost=True,
)


def clone_voice(client: ElevenLabs, prayer_wav: str) -> str:
    print("  [Cloning voice on ElevenLabs...]")
    with open(prayer_wav, "rb") as f:
        voice = client.voices.ivc.create(
            name="oracle_session_voice",
            files=[f],
        )

    print("  [Applying oracle remix...]")
    preview_response = client.text_to_voice.remix(
        voice_id=voice.voice_id,
        voice_description=ORACLE_DESCRIPTION,
        auto_generate_text=True,
    )
    generated_voice_id = preview_response.previews[0].generated_voice_id

    # Save the preview to the voice library so it can be used for TTS
    oracle_voice = client.text_to_voice.create(
        voice_name="oracle_session_voice_remixed",
        voice_description=ORACLE_DESCRIPTION,
        generated_voice_id=generated_voice_id,
    )

    # Delete the raw IVC clone — the remixed voice is what we use from here
    try:
        client.voices.delete(voice.voice_id)
    except Exception:
        pass

    print("  [Oracle voice ready]")
    return oracle_voice.voice_id


def speak(client: ElevenLabs, voice_id: str, text: str) -> bytes:
    chunks = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id=MODEL_ID,
        output_format=OUTPUT_FORMAT,
        voice_settings=ORACLE_VOICE_SETTINGS,
    )
    return b"".join(chunks)


def delete_voice(client: ElevenLabs, voice_id: str) -> None:
    client.voices.delete(voice_id)
    print("  [Voice clone deleted.]")
