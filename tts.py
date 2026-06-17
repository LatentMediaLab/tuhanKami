from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

# ElevenLabs model and output format used for all speech synthesis
MODEL_ID = "eleven_flash_v2_5"
OUTPUT_FORMAT = "pcm_16000"   # 16-bit PCM at 16 kHz — matches SAMPLE_RATE in audio.py

# Prompt fed to ElevenLabs remix to transform the seeker's voice into the Entity's timbre
ENTITY_DESCRIPTION = (
    "A powerful, ancient, and slightly ominous voice. Deep and resonant, carrying the weight of millennia "
    "and the authority of a deity-like entity. Otherworldly and commanding, evoking both awe and reverence. "
    "Make the voice much louder than the original recording, as if the entity is speaking from a vast cavern "
    "or temple. Make the voice so that it is still similar to the original cloned voice, but somehow like an "
    "inverted version of itself. A voice that is majestic but also chilling/scary"
)

# Clone 1: stable and clear — the intelligible divine voice heard through the headset
MAIN_VOICE_SETTINGS = VoiceSettings(
    stability=0.65,
    similarity_boost=1.0,
    style=0.3,
    speed=0.9,
    use_speaker_boost=True,
)

# Clone 2: unstable and slow — the animalistic echo heard through surrounding speakers
ECHO_VOICE_SETTINGS = VoiceSettings(
    stability=0.25,
    similarity_boost=0.9,
    style=0.7,
    speed=0.7,
    use_speaker_boost=True,
)


def clone_voices(client: ElevenLabs, prayer_wav: str) -> tuple[str, str]:
    """
    Upload the prayer once, clone it as the main voice, then remix that clone for the echo.

    Steps: IVC create → remix the same voice → save remixed as a new permanent voice.
    Returns (main_voice_id, echo_voice_id).
    """
    print("  [Cloning voice...]")
    with open(prayer_wav, "rb") as f:
        base = client.voices.ivc.create(name="entity_main_voice", files=[f])
    voice_id_main = base.voice_id
    print("  [Main voice ready — applying entity remix...]")

    preview = client.text_to_voice.remix(
        voice_id=voice_id_main,
        voice_description=ENTITY_DESCRIPTION,
        auto_generate_text=True,
    )
    gen_id = preview.previews[0].generated_voice_id
    entity_voice = client.text_to_voice.create(
        voice_name="entity_echo_voice_remixed",
        voice_description=ENTITY_DESCRIPTION,
        generated_voice_id=gen_id,
    )
    print("  [Echo voice ready]")
    return voice_id_main, entity_voice.voice_id


def speak(client: ElevenLabs, voice_id: str, text: str, settings: VoiceSettings = MAIN_VOICE_SETTINGS) -> bytes:
    # Synthesise text to raw PCM bytes using the given voice and settings.
    chunks = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id=MODEL_ID,
        output_format=OUTPUT_FORMAT,
        voice_settings=settings,
    )
    return b"".join(chunks)


def delete_voices(client: ElevenLabs, *voice_ids: str) -> None:
    # Delete all cloned voices from ElevenLabs. Called at the end of every session.
    for vid in voice_ids:
        try:
            client.voices.delete(vid)
        except Exception:
            pass
    print("  [Voice clones deleted.]")
