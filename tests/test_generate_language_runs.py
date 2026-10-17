"""Task 2: "Auto" language detection and per-span [lang:xx] rendering in /generate."""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from test_generate_engine import _make_fake_engine, _tts_mod, client, no_omnivoice_model  # noqa: F401

PT = "Eu não sei por que você trabalha de noite todos os dias, mas hoje é diferente."


def _engine(monkeypatch, native=False):
    fake = _make_fake_engine()
    fake.supports_native_omnivoice_controls = native
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-engine", fake)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "fake-engine")
    return fake


def test_auto_language_stays_none_for_engines_with_fixed_language_lists(client, monkeypatch, no_omnivoice_model):
    fake = _engine(monkeypatch, native=False)
    res = client.post("/generate", data={"text": PT, "language": "Auto"})
    assert res.status_code == 200, res.text
    assert fake.calls[0][1].get("language") is None


def test_auto_language_is_detected_for_omnivoice_style_engines(client, monkeypatch, no_omnivoice_model):
    fake = _engine(monkeypatch, native=True)
    res = client.post("/generate", data={"text": PT, "language": "Auto"})
    assert res.status_code == 200, res.text
    assert fake.calls[0][1].get("language") == "pt"


def test_explicit_language_is_never_overridden(client, monkeypatch, no_omnivoice_model):
    fake = _engine(monkeypatch, native=True)
    client.post("/generate", data={"text": PT, "language": "en"})
    assert fake.calls[0][1].get("language") == "en"


def test_autodetect_can_be_disabled(client, monkeypatch, no_omnivoice_model):
    fake = _engine(monkeypatch, native=True)
    monkeypatch.setenv("OMNIVOICE_LANG_AUTODETECT", "0")
    client.post("/generate", data={"text": PT, "language": "Auto"})
    assert fake.calls[0][1].get("language") is None


def test_language_tags_render_each_run_in_its_language(client, monkeypatch, no_omnivoice_model):
    fake = _engine(monkeypatch, native=True)
    text = "Eu trabalho de [lang:en]night[/lang] e durmo de dia, mas hoje não."
    res = client.post("/generate", data={"text": text, "language": "pt"})
    assert res.status_code == 200, res.text
    seen = [(t.strip(), kw.get("language")) for t, kw in fake.calls]
    assert seen == [
        ("Eu trabalho de", "pt"),
        ("night", "en"),
        ("e durmo de dia, mas hoje não.", "pt"),
    ]
    assert all("[lang" not in t for t, _ in seen)


def test_language_tags_are_removed_when_streaming(client, monkeypatch, no_omnivoice_model):
    fake = _engine(monkeypatch, native=True)
    res = client.post("/generate", data={
        "text": "Hoje é [lang:en]night[/lang] de novo.", "language": "pt", "stream": "true",
    })
    assert res.status_code == 200, res.text
    assert fake.calls and all("[lang" not in t for t, _ in fake.calls)
