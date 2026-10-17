"""Video narration: segment planning, duration fitting and video export."""
import asyncio
import json
import subprocess

import numpy as np
import pytest
import soundfile as sf

from services import narration as nr

SR = nr.SAMPLE_RATE


def tone(seconds, freq=300.0, amp=0.4):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ── planning ────────────────────────────────────────────────────────────────

def test_script_without_segments_spans_the_whole_video():
    segs = nr.plan_segments("Hello there", None, 10.0)
    assert segs == [{"id": "seg_0", "text": "Hello there", "start": 0.0, "end": 10.0}]


def test_segment_ends_default_to_next_start_and_video_end():
    segs = nr.plan_segments("", [{"text": "b", "start": 4}, {"text": "a", "start": 0}], 10.0)
    assert [(s["text"], s["start"], s["end"]) for s in segs] == [("a", 0, 4), ("b", 4, 10)]


@pytest.mark.parametrize("bad", [
    [{"text": "a", "start": 0, "end": 5}, {"text": "b", "start": 4}],  # overlap
    [{"text": "a", "start": 12}],  # beyond the video
    [{"text": "a", "start": 0, "end": 0.1}],  # too short
    [{"text": "  ", "start": 0}],  # nothing to say
])
def test_invalid_segments_are_rejected(bad):
    with pytest.raises(nr.NarrationError):
        nr.plan_segments("", bad, 10.0)


def test_empty_script_is_rejected():
    with pytest.raises(nr.NarrationError):
        nr.plan_segments("  ", None, 10.0)


# ── duration fitting ────────────────────────────────────────────────────────

def _render(segs, durations, video_s):
    async def synth(text):
        return tone(durations[text]), SR

    return asyncio.run(nr.render_track(segs, synth, video_s))


def _active(track, a, b):
    seg = track[int(a * SR):int(b * SR)]
    return float(np.mean(np.abs(seg) > 0.01))


def test_long_speech_is_sped_up_to_fit_its_slot():
    segs = nr.plan_segments("", [{"text": "long", "start": 0, "end": 5}, {"text": "next", "start": 5}], 10.0)
    track, report = _render(segs, {"long": 6.0, "next": 3.0}, 10.0)
    first = report["segments"][0]
    assert first["audio_rate"] == pytest.approx(1.2, abs=0.02)
    assert not first["trimmed"]
    assert len(track) == 10 * SR
    # Nothing leaks into the next segment's slot before it starts.
    assert _active(track, 4.2, 5.0) > 0.9
    assert _active(track, 8.8, 10.0) < 0.05  # "next" (slowed to ~3.5s) ends by 8.6


def test_short_speech_is_slowed_a_little_but_not_stretched_to_fill():
    segs = nr.plan_segments("", [{"text": "short", "start": 0, "end": 10}], 10.0)
    track, report = _render(segs, {"short": 9.0}, 10.0)
    rate = report["segments"][0]["audio_rate"]
    assert 0.85 <= rate < 1.0
    assert _active(track, 0.2, 9.0) > 0.9


def test_speech_far_too_long_is_trimmed_and_reported():
    segs = nr.plan_segments("", [{"text": "huge", "start": 0, "end": 2}, {"text": "n", "start": 2}], 6.0)
    track, report = _render(segs, {"huge": 9.0, "n": 1.0}, 6.0)
    assert report["needs_trim"] and report["segments"][0]["trimmed"]
    assert report["segments"][0]["audio_rate"] <= 1.8
    # The overflow never spills into the next segment's slot.
    assert float(np.max(np.abs(track[int(2.0 * SR) - 10:int(2.0 * SR) + 10]))) < 0.5


def test_segments_start_at_their_marked_times():
    segs = nr.plan_segments("", [{"text": "a", "start": 1, "end": 3}, {"text": "b", "start": 6, "end": 8}], 10.0)
    track, _ = _render(segs, {"a": 2.0, "b": 2.0}, 10.0)
    assert _active(track, 0.0, 0.9) == 0.0
    assert _active(track, 1.2, 2.8) > 0.9
    assert _active(track, 3.5, 5.5) == 0.0
    assert _active(track, 6.2, 7.8) > 0.9


# ── export ──────────────────────────────────────────────────────────────────

def _ffmpeg():
    from services.ffmpeg_utils import find_ffmpeg
    path = find_ffmpeg()
    if not path:
        pytest.skip("ffmpeg not available")
    return path


@pytest.fixture
def video(tmp_path):
    ff = _ffmpeg()
    out = tmp_path / "in.mp4"
    subprocess.run([
        ff, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out),
    ], check=True)
    return str(out)


def _probe(path):
    from services.ffmpeg_utils import find_ffprobe
    res = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries",
                          "stream=codec_type,duration", "-of", "json", path],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)["streams"]


def _audio_of(path, tmp_path, name):
    wav = tmp_path / name
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                    "-vn", "-ar", str(SR), "-ac", "1", str(wav)], check=True)
    data, _ = sf.read(str(wav), dtype="float32")
    return data


def _freq_energy(data, freq):
    spec = np.abs(np.fft.rfft(data * np.hanning(len(data))))
    bins = np.fft.rfftfreq(len(data), 1 / SR)
    band = (bins > freq - 40) & (bins < freq + 40)
    return float(spec[band].sum())


@pytest.mark.parametrize("keep", [False, True])
def test_export_replaces_or_mixes_original_audio(video, tmp_path, keep):
    narration = tmp_path / "vo.wav"
    nr.write_wav(str(narration), np.concatenate([tone(3.0, 300.0), np.zeros(SR, dtype=np.float32)]))
    out = tmp_path / ("keep.mp4" if keep else "mute.mp4")
    asyncio.run(nr.mux(video, str(narration), str(out), keep_original=keep, original_volume=0.5))
    streams = _probe(str(out))
    assert sorted(s["codec_type"] for s in streams) == ["audio", "video"]
    video_s = next(float(s["duration"]) for s in streams if s["codec_type"] == "video")
    audio_s = next(float(s["duration"]) for s in streams if s["codec_type"] == "audio")
    assert abs(video_s - 4.0) < 0.3 and abs(audio_s - video_s) < 0.3
    audio = _audio_of(str(out), tmp_path, "o.wav")
    assert _freq_energy(audio, 300.0) > 0  # narration present
    original_tone = _freq_energy(audio, 1000.0)
    narration_tone = _freq_energy(audio, 300.0)
    if keep:
        assert original_tone > narration_tone * 0.05
    else:
        assert original_tone < narration_tone * 0.02


def test_export_without_original_audio_still_works_when_keep_requested(tmp_path):
    ff = _ffmpeg()
    silent = tmp_path / "silent.mp4"
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=160x120:rate=10:duration=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(silent)], check=True)
    narration = tmp_path / "vo.wav"
    nr.write_wav(str(narration), tone(3.0))
    out = tmp_path / "out.mp4"
    asyncio.run(nr.mux(str(silent), str(narration), str(out), keep_original=True))
    assert "audio" in [s["codec_type"] for s in _probe(str(out))]


# ── HTTP layer ──────────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import narration as router_mod

    monkeypatch.setattr(router_mod, "OUTPUTS_DIR", str(tmp_path / "outputs"))
    app = FastAPI()
    app.include_router(router_mod.router)
    router_mod._JOBS.clear()
    return TestClient(app, client=("127.0.0.1", 50000)), router_mod


def test_upload_rejects_non_video_and_empty_script(api):
    client, _ = api
    r = client.post("/narration/jobs", data={"script": "hi"}, files={"video": ("a.txt", b"x", "text/plain")})
    assert r.status_code == 415
    r = client.post("/narration/jobs", data={"script": " "}, files={"video": ("a.mp4", b"x", "video/mp4")})
    assert r.status_code == 422


def test_job_runs_end_to_end_and_serves_the_video(api, video, tmp_path):
    client, mod = api
    import shutil, os

    job = {"id": "abc123", "status": "queued", "progress": {"done": 0, "total": 0}, "error": None,
           "report": None, "created_at": 0.0, "out_ext": ".mp4"}
    directory = mod._job_dir("abc123")
    os.makedirs(directory)
    job["video_path"] = os.path.join(directory, "input.mp4")
    shutil.copy(video, job["video_path"])
    mod._JOBS["abc123"] = job

    async def synth(text):
        return tone(2.0), SR

    segments = [{"text": "one", "start": 0, "end": 2}, {"text": "two", "start": 2}]
    asyncio.run(mod._run(job, synth, segments, "", True, 0.3))
    body = client.get("/narration/jobs/abc123").json()
    assert body["status"] == "done", body
    assert body["progress"] == {"done": 2, "total": 2}
    assert len(body["report"]["segments"]) == 2
    res = client.get("/narration/jobs/abc123/video")
    assert res.status_code == 200 and res.headers["content-type"] == "video/mp4"
    assert len(res.content) > 1000
    assert client.delete("/narration/jobs/abc123").status_code == 200
    assert client.get("/narration/jobs/abc123").status_code == 404


def test_job_reports_bad_segments_as_error(api, video):
    client, mod = api
    import shutil, os

    directory = mod._job_dir("bad000")
    os.makedirs(directory)
    job = {"id": "bad000", "status": "queued", "progress": {"done": 0, "total": 0}, "error": None,
           "report": None, "created_at": 0.0, "out_ext": ".mp4",
           "video_path": os.path.join(directory, "input.mp4")}
    shutil.copy(video, job["video_path"])
    mod._JOBS["bad000"] = job

    async def synth(text):
        return tone(1.0), SR

    asyncio.run(mod._run(job, synth, [{"text": "x", "start": 99}], "", False, 0.2))
    assert job["status"] == "error" and "outside the video" in job["error"]
