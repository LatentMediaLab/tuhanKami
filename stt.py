import whisper

model = whisper.load_model("small")


def transcribe(wav_path: str) -> tuple[str, str]:
    result = model.transcribe(wav_path, fp16=False)
    return result["text"].strip(), result["language"]
