import whisper

_model = whisper.load_model("small")


def transcribe(wav_path: str) -> tuple[str, str]:
    """Returns (transcribed_text, language_code) e.g. ("Hello", "en") or ("こんにちは", "ja")."""
    result = _model.transcribe(wav_path, fp16=False)
    return result["text"].strip(), result["language"]
