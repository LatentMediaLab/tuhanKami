import whisper

model = whisper.load_model("small")


def transcribe(wav_path: str, language: str | None = None) -> tuple[str, str]:
    # language, if given, hints Whisper's decoder instead of relying on auto-detection.
    result = model.transcribe(wav_path, fp16=False, language=language)
    return result["text"].strip(), result["language"]
