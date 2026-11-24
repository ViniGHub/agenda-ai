import pytest

from agenda_ia.ai.llm import LLMError
from agenda_ia.ai.speech import UnavailableTranscriber, clean_transcript


def test_speech_segments_become_the_text():
    data = {"text": "ignorado", "segments": [
        {"text": " Marca dentista", "no_speech_prob": 0.01},
        {"text": " quinta às 15h.", "no_speech_prob": 0.02},
    ]}
    assert clean_transcript(data) == "Marca dentista quinta às 15h."


def test_segments_marked_as_not_speech_are_dropped():
    data = {"segments": [
        {"text": " Marca dentista quinta às 15h.", "no_speech_prob": 0.05},
        {"text": " Legendas pela comunidade.", "no_speech_prob": 0.92},
    ]}
    assert clean_transcript(data) == "Marca dentista quinta às 15h."


@pytest.mark.parametrize(
    "text",
    [
        " Obrigado.",  # what real Whisper returned for 3 s of noise, with no_speech_prob = 0
        "Legendas pela comunidade Amara.org",
        "Obrigado por assistir!",
        "   ",
        "",
    ],
)
def test_invented_captions_and_silence_become_nothing(text):
    assert clean_transcript({"text": text, "segments": [{"text": text, "no_speech_prob": 0}]}) == ""


def test_text_without_segments_still_works():
    assert clean_transcript({"text": "  o que tenho   amanhã? "}) == "o que tenho amanhã?"


def test_bad_probabilities_are_not_trusted_to_drop_speech():
    data = {"segments": [{"text": "dentista amanhã às 9h", "no_speech_prob": "n/a"}]}
    assert clean_transcript(data) == "dentista amanhã às 9h"


def test_unavailable_transcriber_fails_loudly():
    with pytest.raises(LLMError):
        UnavailableTranscriber("sem chave").transcribe(b"x", "a.webm")
