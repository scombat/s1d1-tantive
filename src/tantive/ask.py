"""What to classify: the current ask of a LiteLLM state, the text of OpenAI content, a flattened state (spec § 3.5).

LiteLLM's classifier context ends with `Classify this message:` followed by the user's current message; everything
before it is context quoted for an LLM classifier and must never reach the model. Other callers send the text alone.
"""

from __future__ import annotations

MARKER = "\nClassify this message:\n"


class EmptyAsk(ValueError):
    """There is no text to classify once the context is removed."""


def current_ask(text: str) -> str:
    """The text after the last MARKER (the whole text when absent), trailing whitespace removed."""
    _, _, after = text.rpartition(MARKER)
    ask = after.rstrip()
    if not ask.strip():
        raise EmptyAsk("no text to classify")
    return ask


def text_of(content: object) -> str:
    """OpenAI message content: a string as is, a list of parts keeping the text ones, anything else empty."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            p["text"]
            for p in content
            if isinstance(p, dict) and p.get("type") in ("text", "input_text") and isinstance(p.get("text"), str)
        ]
        return "\n".join(texts)
    return ""


def flatten_state(state: object) -> str:
    """A System One state as text: strings as is, lists joined by newlines, objects as `key: value` lines."""
    if isinstance(state, str):
        return state
    if isinstance(state, list):
        return "\n".join(flatten_state(item) for item in state)
    if isinstance(state, dict):
        return "\n".join(f"{key}: {flatten_state(value)}" for key, value in state.items())
    return str(state)
