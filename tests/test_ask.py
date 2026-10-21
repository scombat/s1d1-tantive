import pytest

from tantive.ask import MARKER, EmptyAsk, current_ask, flatten_state, text_of


def test_marker_takes_last_block():
    text = "Caller system prompt:\nx\nClassify this message:\nold\n\nClassify this message:\nnew ask"
    assert current_ask(text) == "new ask"


def test_no_marker_returns_whole_text():
    assert current_ask("plain prompt") == "plain prompt"


def test_marker_with_empty_ask():
    with pytest.raises(EmptyAsk):
        current_ask("context\nClassify this message:\n   ")


def test_blank_text_without_marker_is_empty_ask():
    with pytest.raises(EmptyAsk):
        current_ask("  \n")


def test_only_trailing_whitespace_is_stripped():
    assert current_ask(f"ctx{MARKER}  indented ask\n\n") == "  indented ask"


def test_text_of_parts():
    parts = [{"type": "text", "text": "a"}, {"type": "image_url", "image_url": {}}, {"type": "input_text", "text": "b"}]
    assert text_of(parts) == "a\nb"


def test_text_of_string_and_none():
    assert text_of("plain") == "plain"
    assert text_of(None) == ""


def test_state_object_is_flattened():
    assert flatten_state({"ticket": "Refund?", "tags": ["a", "b"]}) == "ticket: Refund?\ntags: a\nb"


def test_state_list():
    assert flatten_state(["x", {"k": 1}]) == "x\nk: 1"


def test_state_scalars():
    assert flatten_state(42) == "42"
    assert flatten_state("as is") == "as is"
