"""Several recordings of one voice merge into one saved clone profile."""
import io
import os

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

SR = 24000


def speech_wav(seconds=6.0, freq=220.0) -> bytes:
    t = np.arange(int(seconds * SR)) / SR
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)
    audio = (0.3 * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, SR, format="WAV")
    return buf.getvalue()


def silence_wav(seconds=3.0) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(seconds * SR), dtype=np.float32), SR, format="WAV")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from api.routers import profile_references, profiles
    from core import db

    voices = tmp_path / "voices"
    monkeypatch.setattr(profiles, "VOICES_DIR", str(voices))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "profiles.db"))
    db.init_db()

    async def fake_transcribe(path):
        return "merged transcript"

    monkeypatch.setattr(profiles, "_auto_transcribe_reference", fake_transcribe)
    monkeypatch.setattr(profiles.event_bus, "emit", lambda *a: None)
    app = FastAPI()
    app.include_router(profiles.router)
    app.include_router(profile_references.router)
    return TestClient(app), voices


def create(client, extras, first=None):
    files = [("ref_audio", ("a.wav", first or speech_wav(6, 200), "audio/wav"))]
    files += [("extra_ref_audios", (n, b, "audio/wav")) for n, b in extras]
    return client.post("/profiles", data={"name": "Multi"}, files=files)


def test_create_with_several_recordings_merges_into_one_clip(env):
    client, voices = env
    r = create(client, [("b.wav", speech_wav(6, 300)), ("c.wav", speech_wav(6, 400))])
    assert r.status_code == 200, r.text
    profile = r.json()
    merged = voices / profile["ref_audio_path"]
    assert merged.suffix == ".wav"
    info = sf.info(str(merged))
    from omnivoice.utils.audio import CLONE_REF_TEXT_MAX_SECONDS
    assert 8.0 < info.duration <= CLONE_REF_TEXT_MAX_SECONDS  # engine takes it whole, with transcript
    assert profile["ref_text"] == "merged transcript"
    refs = client.get(f"/profiles/{profile['id']}/references").json()
    assert [x["status"] for x in refs["references"]] == ["ok"] * 3
    assert refs["source_seconds"] > 10


def test_unusable_recordings_are_reported_but_do_not_block(env):
    client, _ = env
    r = create(client, [("quiet.wav", silence_wav())])
    assert r.status_code == 200, r.text
    refs = client.get(f"/profiles/{r.json()['id']}/references").json()["references"]
    assert [x["status"] for x in refs] == ["ok", "rejected"]
    assert refs[1]["reason"]


def test_all_unusable_recordings_fail_without_leftovers(env):
    client, voices = env
    r = create(client, [("quiet.wav", silence_wav())], first=silence_wav())
    assert r.status_code == 422
    assert client.get("/profiles").json() == []
    assert not voices.exists() or list(voices.iterdir()) == []


def test_single_recording_profile_is_unchanged_legacy_path(env):
    client, voices = env
    r = client.post("/profiles", data={"name": "One"},
                    files={"ref_audio": ("a.wav", speech_wav(), "audio/wav")})
    assert r.status_code == 200
    assert r.json()["ref_audio_path"].endswith(".wav")
    assert client.get(f"/profiles/{r.json()['id']}/references").json()["references"] == []


def test_add_recording_to_legacy_profile_registers_original_as_first_source(env):
    client, voices = env
    legacy = client.post("/profiles", data={"name": "One"},
                         files={"ref_audio": ("a.wav", speech_wav(6, 200), "audio/wav")}).json()
    r = client.post(f"/profiles/{legacy['id']}/references",
                    files=[("ref_audios", ("b.wav", speech_wav(6, 330), "audio/wav"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["references"]) == 2
    assert body["ref_audio_path"] != legacy["ref_audio_path"]
    assert sf.info(str(voices / body["ref_audio_path"])).duration > 6


def test_delete_reference_rebuilds_and_last_one_is_protected(env):
    client, voices = env
    p = create(client, [("b.wav", speech_wav(6, 300))]).json()
    refs = client.get(f"/profiles/{p['id']}/references").json()["references"]
    r = client.delete(f"/profiles/{p['id']}/references/{refs[0]['id']}")
    assert r.status_code == 200, r.text
    left = r.json()["references"]
    assert len(left) == 1
    assert client.delete(f"/profiles/{p['id']}/references/{left[0]['id']}").status_code == 409
    assert client.delete(f"/profiles/{p['id']}/references/nope").status_code == 404


def test_delete_profile_removes_source_files_and_rows(env):
    client, voices = env
    p = create(client, [("b.wav", speech_wav(6, 300))]).json()
    assert any("-src-" in n for n in os.listdir(voices))
    assert client.delete(f"/profiles/{p['id']}").status_code == 200
    assert not any(n.startswith(p["id"]) for n in os.listdir(voices))
    from core.db import db_conn
    with db_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM voice_profile_refs").fetchone()[0] == 0


def test_designed_voice_rejects_references(env):
    client, _ = env
    from core.db import db_conn
    with db_conn() as conn:
        conn.execute("INSERT INTO voice_profiles (id,name,kind,created_at) VALUES ('d1','D','design',0)")
    r = client.post("/profiles/d1/references",
                    files=[("ref_audios", ("b.wav", speech_wav(), "audio/wav"))])
    assert r.status_code == 409
