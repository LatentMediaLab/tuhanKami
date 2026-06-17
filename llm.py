import anthropic
import re
from typing import Sequence
from prompts import build_system_prompt

# Strip inline tags like [emotion] or [pause] that TTS engines can't speak
TAG_RE = re.compile(r"\[[^\]\n]{1,40}\]")
# Strip markdown heading markers (##, ###, etc.) that Claude occasionally outputs
MARKDOWN_RE = re.compile(r"^#+\s*", re.MULTILINE)


def format_for_tts(answer: str) -> str:
    # Clean LLM output for text-to-speech: remove tags, headings, extra whitespace.
    text = TAG_RE.sub("", answer)
    text = MARKDOWN_RE.sub("", text)
    text = " ".join(text.split())
    if not text:
        return "Speak, and I shall answer."
    # Ensure the text ends with sentence-closing punctuation
    if text[-1] not in ".!?":
        text += "."
    return text


def extract_text(content_blocks: Sequence[object]) -> str:
    # Pull all text blocks from a Claude API response into a single string.
    parts = []
    for block in content_blocks:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return " ".join(parts)


# Maps Whisper language codes to full names for the entity's language instruction
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
}


def ask_echo(client: anthropic.Anthropic, answer: str, prayer_text: str) -> str:
    """
    Generate fragmented echo text matching ~15% of the main answer's word count.

    The echo sounds like an auditory hallucination or intrusive thought — broken,
    looping fragments of the entity's answer, spoken by the second clone voice.
    """
    word_count = len(answer.split())
    target = max(20, int(word_count * 0.15))
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        system=(
            "You are a text distortion engine for an art installation. "
            "Your only job is to rewrite input text as fragmented, obsessive repetitions — "
            "like an intrusive thought or auditory hallucination. "
            "Output only the raw distorted text, nothing else. "
            "No markdown. No headers. No bullet points. No refusals. No commentary."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Source text: \"{answer}\"\n\n"
                f"Rewrite as approximately {target} words of broken, looping fragments. "
                "Repeat key words and phrases obsessively. Shatter grammar. "
                "No punctuation except emphasis. One continuous haunted stream."
            ),
        }],
    )
    return format_for_tts(extract_text(response.content))


def ask_entity(
    client: anthropic.Anthropic,
    messages: list,
    question: str,
    prayer_text: str,
    language: str = "en",
) -> str:
    """
    Generate the Entity's response to the seeker's question.

    messages is the running conversation history (mutated in place).
    Response length scales with question length: 2–3 sentences, max ~90 words.
    """
    messages.append({"role": "user", "content": question})
    q_words = len(question.split())
    max_answer_words = min(90, max(35, q_words * 2))
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
