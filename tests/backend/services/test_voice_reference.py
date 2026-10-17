"""Multi-reference voice profiles: clean each clip, then merge into one reference."""
import numpy as np
import pytest
import soundfile as sf

from services import voice_reference as vr

SR = vr.TARGET_SR


def _speechlike(seconds: float, freq: float = 220.0, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    # AM-modulated tone: energy envelope like syllables, not a constant hum.
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)
    return (amp * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def _write(path, audio, sr=SR):
    sf.write(str(path), audio, sr)
    return str(path)


def test_clean_clip_trims_edges_and_normalizes_level():
    raw = np.concatenate([_silence(1.0), _speechlike(3.0, amp=0.02), _silence(1.0)])
    cleaned = vr.clean_clip(raw)
    assert 2.5 < len(cleaned) / SR < 3.6  # silence trimmed
    rms = float(np.sqrt(np.mean(cleaned**2)))
    assert 0.05 < rms < 0.2  # lifted from the very quiet source
    assert float(np.max(np.abs(cleaned))) <= 0.99


def test_clean_clip_rejects_silence():
    with pytest.raises(vr.ReferenceRejected):
        vr.clean_clip(_silence(4.0))


def test_clean_clip_rejects_too_short():
    with pytest.raises(vr.ReferenceRejected):
        vr.clean_clip(_speechlike(0.4))


def test_split_speech_drops_fragments_shorter_than_minimum():
    audio = np.concatenate([
        _speechlike(2.5), _silence(0.8), _speechlike(0.3), _silence(0.8), _speechlike(2.0)
    ])
    spans = vr.split_speech(audio)
    lengths = [(e - s) / SR for s, e in spans]
    assert len(spans) == 2
    assert all(length >= vr.MIN_SEGMENT_S for length in lengths)


def test_combine_respects_budget_and_uses_every_source():
    a = _speechlike(9.0, 200)
    b = _speechlike(9.0, 300)
    c = _speechlike(9.0, 400)
    merged, report = vr.combine_clips([a, b, c], max_seconds=15.0)
    assert len(merged) / SR <= 15.0 + 1e-6
    assert len(merged) / SR > 10.0
    assert {p["source"] for p in report["picked"]} == {0, 1, 2}


def test_combine_single_short_clip_is_returned_whole():
    a = _speechlike(4.0)
    merged, report = vr.combine_clips([a], max_seconds=15.0)
    assert abs(len(merged) / SR - 4.0) < 0.2
    assert len(report["picked"]) == 1


def test_build_reference_from_files_reports_rejects_and_keeps_good(tmp_path):
    good1 = _write(tmp_path / "a.wav", np.concatenate([_silence(0.5), _speechlike(5.0), _silence(0.5)]))
    good2 = _write(tmp_path / "b.wav", _speechlike(6.0, 330))
    bad = _write(tmp_path / "c.wav", _silence(3.0))
    out = tmp_path / "combined.wav"
    report = vr.build_reference([good1, good2, bad], str(out), max_seconds=15.0)
    assert out.is_file()
    info = sf.info(str(out))
    assert info.samplerate == SR and info.channels == 1
    assert report["duration"] == pytest.approx(info.duration, abs=0.05)
    statuses = [s["status"] for s in report["sources"]]
    assert statuses == ["ok", "ok", "rejected"]
    assert report["sources"][2]["reason"]


def test_build_reference_fails_when_nothing_usable(tmp_path):
    bad = _write(tmp_path / "c.wav", _silence(3.0))
    with pytest.raises(vr.ReferenceRejected):
        vr.build_reference([bad], str(tmp_path / "o.wav"))
