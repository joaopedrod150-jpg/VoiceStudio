"""Pronunciation cases: pt-BR, English and mixed sentences (fixtures for Task 2)."""
import pytest

from services.lang_spans import detect_language, has_lang_tags, split_lang_runs, strip_lang_tags

PT_BR = [
    "O rato roeu a roupa do rei de Roma, e a rainha ficou muito brava.",
    "Hoje eu vou correr no parque porque o tempo está muito bom.",
    "Ela disse que o carro de corrida não era para a gente.",
    "Eu não sei por que você trabalha de noite todos os dias.",
]
EN = [
    "The night was long and we were not sure what to do next.",
    "It is a very rare thing to see the stars on a night like this.",
    "She said that the road to the river was closed because of the rain.",
]
ES = ["El perro de mi vecino no quiere comer porque está muy cansado."]
MIXED = "Eu trabalho de [lang:en]night[/lang] e durmo de dia, mas hoje não."


@pytest.mark.parametrize("text", PT_BR)
def test_detects_portuguese(text):
    assert detect_language(text) == "pt"


@pytest.mark.parametrize("text", EN)
def test_detects_english(text):
    assert detect_language(text) == "en"


@pytest.mark.parametrize("text", ES)
def test_spanish_is_not_portuguese(text):
    assert detect_language(text) == "es"


@pytest.mark.parametrize("text", ["night", "OK", "Rio", "1 2 3 4 5", ""])
def test_short_or_ambiguous_text_declines(text):
    assert detect_language(text) is None


def test_portuguese_sentence_with_english_loan_word_stays_portuguese():
    assert detect_language("Eu gosto de trabalhar de night porque o silêncio é muito bom.") == "pt"


def test_tagged_span_is_ignored_by_detection():
    assert detect_language(MIXED) == "pt"


def test_split_runs_assigns_language_per_span():
    runs = split_lang_runs(MIXED, "pt")
    assert [lang for _, lang in runs] == ["pt", "en", "pt"]
    assert runs[1][0] == "night"
    assert "".join(t for t, _ in runs) == strip_lang_tags(MIXED)


def test_untagged_text_is_one_run_and_unchanged():
    text = "Olá, tudo bem?"
    assert not has_lang_tags(text)
    assert split_lang_runs(text, None) == [(text, None)]
    assert strip_lang_tags(text) == text


def test_regional_tag_is_accepted():
    assert split_lang_runs("[lang:pt-BR]rato[/lang]", None) == [("rato", "pt-BR")]
