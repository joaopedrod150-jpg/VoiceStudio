"""Video narration: import a video, write (or time-mark) a script, get the video
back with a voice track fitted to its duration. No lip-sync.

    POST   /narration/jobs              multipart: video + script/segments + voice -> {id}
    GET    /narration/jobs/{id}         status, progress and the fit report
    GET    /narration/jobs/{id}/video   the finished video
    DELETE /narration/jobs/{id}         remove the job and its files

Speech comes from the app's own ``POST /generate`` (called in-process), so the
chosen voice profile (including multi-recording ones), engine, language,
text normalisation, pronunciation dictionary and provenance mark all apply
exactly as in Voice cloning. Timing is the dubbing planner's
(``services/narration.py``).
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import shutil
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from api.dependencies import require_admin
from core.config import OUTPUTS_DIR
from services import narration as nr

logger = logging.getLogger("omnivoice.narration")
router = APIRouter(dependencies=[Depends(require_admin)])

_VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}
_MAX_VIDEO_BYTES = 4 * 1024 ** 3
_CHUNK = 1024 * 1024
_JOBS: dict[str, dict] = {}
_TASKS: set = set()
_SKIP_HEADERS = {"host", "content-length", "content-type", "transfer-encoding", "connection", "accept-encoding"}


def _root() -> str:
    return os.path.join(OUTPUTS_DIR, "narration")


def _job_dir(job_id: str) -> str:
    if not job_id.isalnum() or len(job_id) > 32:
        raise HTTPException(status_code=404, detail="That narration job doesn't exist.")
    return os.path.join(_root(), job_id)


def _job_or_404(job_id: str) -> dict:
    _job_dir(job_id)
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="That narration job doesn't exist.")
    return job


def _public(job: dict) -> dict:
    return {k: job[k] for k in ("id", "status", "progress", "error", "report", "created_at") if k in job}


def _make_synth(app, headers: dict, form: dict) -> nr.Synth:
    """Synthesize text through this app's own /generate endpoint."""
    import httpx
    import numpy as np
    import soundfile as sf

    async def synth(text: str):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            res = await client.post("/generate", data=dict(form, text=text), headers=headers, timeout=None)
        if res.status_code != 200:
            try:
                detail = res.json().get("detail")
            except Exception:  # noqa: BLE001
                detail = None
            raise nr.NarrationError(str(detail or f"Voice generation failed ({res.status_code})."))
        data, sr = sf.read(io.BytesIO(res.content), dtype="float32", always_2d=True)
        return np.asarray(data.mean(axis=1), dtype=np.float32), int(sr)

    return synth


async def _probe_duration(path: str) -> float:
    from services.ffmpeg_utils import find_ffprobe, spawn_subprocess, local_inputs_only

    ffprobe = find_ffprobe()
    if not ffprobe:
        raise nr.NarrationError("ffprobe is not available. Install ffmpeg from Settings -> Models.")
    cmd = local_inputs_only([ffprobe, "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", path], "ffprobe")
    proc = await spawn_subprocess(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except ValueError:
        raise nr.NarrationError("That file could not be read as a video.")


async def _run(job: dict, synth: nr.Synth, segments, script: str, keep_original: bool,
               original_volume: float) -> None:
    job["status"] = "running"
    directory = _job_dir(job["id"])
    try:
        video = job["video_path"]
        video_s = await _probe_duration(video)
        segs = nr.plan_segments(script, segments, video_s)
        job["progress"] = {"done": 0, "total": len(segs)}

        def progress(done: int, total: int) -> None:
            job["progress"] = {"done": done, "total": total}

        track, report = await nr.render_track(segs, synth, video_s, progress=progress)
        wav = os.path.join(directory, "narration.wav")
        await asyncio.to_thread(nr.write_wav, wav, track)
        out = os.path.join(directory, "output" + job["out_ext"])
        await nr.mux(video, wav, out, keep_original=keep_original, original_volume=original_volume)
        report["video_seconds"] = round(video_s, 2)
        job.update(status="done", report=report, output_path=out)
        with contextlib.suppress(OSError):
            os.remove(video)  # the finished video is the deliverable
    except nr.NarrationError as exc:
        job.update(status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("narration job failed")
        job.update(status="error", error=f"Narration failed: {exc}")


@router.post("/narration/jobs")
async def create_job(
    request: Request,
    video: UploadFile = File(...),
    script: str = Form(""),
    segments: str = Form(""),
    profile_id: str = Form(""),
    language: str = Form("Auto"),
    engine: str = Form(""),
    instruct: str = Form(""),
    keep_original: bool = Form(False),
    original_volume: float = Form(0.25),
):
    ext = os.path.splitext(video.filename or "")[1].lower()
    if ext not in _VIDEO_EXTS:
        raise HTTPException(status_code=415, detail="Choose a video file: MP4, MOV, MKV, WebM or AVI.")
    parsed = None
    if segments.strip():
        try:
            parsed = json.loads(segments)
            if not isinstance(parsed, list):
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=422, detail="segments must be a JSON list.")
    if not parsed and not script.strip():
        raise HTTPException(status_code=422, detail="Write the narration text first.")

    job_id = uuid.uuid4().hex[:12]
    directory = _job_dir(job_id)
    os.makedirs(directory, exist_ok=True)
    video_path = os.path.join(directory, "input" + ext)
    total = 0
    try:
        with open(video_path, "wb") as out:
            while chunk := await video.read(_CHUNK):
                total += len(chunk)
                if total > _MAX_VIDEO_BYTES:
                    raise HTTPException(status_code=413, detail="That video is too large.")
                out.write(chunk)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    form = {"language": language or "Auto"}
    if profile_id:
        form["profile_id"] = profile_id
    if engine:
        form["engine"] = engine
    if instruct:
        form["instruct"] = instruct
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS}
    job = {
        "id": job_id, "status": "queued", "progress": {"done": 0, "total": 0}, "error": None,
        "report": None, "created_at": time.time(), "video_path": video_path,
        "out_ext": ".mp4" if ext in (".mp4", ".m4v", ".mov") else ".mkv",
    }
    _JOBS[job_id] = job
    task = asyncio.create_task(_run(
        job, _make_synth(request.app, headers, form), parsed, script, keep_original, original_volume,
    ))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return _public(job)


@router.get("/narration/jobs/{job_id}")
def get_job(job_id: str):
    return _public(_job_or_404(job_id))


@router.get("/narration/jobs/{job_id}/video")
def get_video(job_id: str):
    job = _job_or_404(job_id)
    path = job.get("output_path")
    if job["status"] != "done" or not path or not os.path.isfile(path):
        raise HTTPException(status_code=409, detail="The narrated video isn't ready yet.")
    media = "video/mp4" if path.endswith(".mp4") else "video/x-matroska"
    return FileResponse(path, media_type=media, filename="narrated" + os.path.splitext(path)[1])


@router.delete("/narration/jobs/{job_id}")
def delete_job(job_id: str):
    _job_or_404(job_id)
    _JOBS.pop(job_id, None)
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    return {"id": job_id, "deleted": True}
