import anthropic
import re
from typing import Sequence
from prompts import build_system_prompt

TAG_RE = re.compile(r"\[[^\]\n]{1,40}\]")


def format_for_tts(answer: str) -> str:
    # Keep output compact and TTS-friendly even when the model drifts from format rules.
    # Remove optional audio tags for lower-latency, cleaner synthesis.
    text = TAG_RE.sub("", answer)
    text = " ".join(text.split())
    if not text:
        return "Speak, and I shall answer."
    if text[-1] not in ".!?":
        text += "."
    return text


def extract_text(content_blocks: Sequence[object]) -> str:
    parts = []
    for block in content_blocks:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return " ".join(parts)


LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
}


def ask_entity(
    client: anthropic.Anthropic,
    messages: list,
    question: str,
    prayer_text: str,
    language: str = "en",
) -> str:
    messages.append({"role": "user", "content": question})
    q_words = len(question.split())
    max_answer_words = min(90, max(35, q_words * 3))
    lang_name = LANGUAGE_NAMES.get(language, language)
    system = build_system_prompt(prayer_text) + (
        f"\n\nRespond exclusively in {lang_name}. "
        "Respond in 2 to 3 complete sentences, "
        f"no more than {max_answer_words} words. End on a complete sentence."
    )
    max_tokens = min(200, max(100, int(max_answer_words * 2.0)))
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    answer = format_for_tts(extract_text(response.content))
    messages.append({"role": "assistant", "content": answer})
    return answer
