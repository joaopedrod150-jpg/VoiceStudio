"""Video narration: script (optionally split into timed segments) -> voice track
fitted to the video -> final video.

No lip-sync. The spoken audio is fitted to the *time* slots only. The timing
maths is the dubbing pipeline's own ``fit_planner.plan_fit`` (audio-only mode,
``allow_video_retime=False``), so a narration segment is sped up or slowed
(pitch-preserving, ffmpeg ``atempo``) exactly like a dubbed one, and any audio
that still does not fit its slot is trimmed with a short fade and reported.

Pure planning and mixing live here; the HTTP/job layer is
``api/routers/narration.py``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable, Optional, Sequence

import numpy as np

from services.fit_planner import FitParams, FitPlan, plan_fit

logger = logging.getLogger("omnivoice.narration")

SAMPLE_RATE = 24000
FADE_S = 0.08
MAX_SEGMENTS = 200
MIN_SLOT_S = 0.3
# The dub planner's audio-only ceiling is 1.8x; keep the same guard rail.
FIT_PARAMS = FitParams(allow_video_retime=False)

Synth = Callable[[str], Awaitable["tuple[np.ndarray, int]"]]


class NarrationError(ValueError):
    """The request cannot be turned into a narration (bad segments, no video...)."""


def plan_segments(
    script: str, segments: Optional[Sequence[dict]], video_s: float
) -> list[dict]:
    """Validate and complete the timed segments.

    ``segments`` items are ``{"text", "start", "end"?}`` in seconds. A missing
    ``end`` runs to the next start (or the video end). With no segments the
    whole script is one segment spanning the video. Segments are sorted by
    start; overlaps and starts beyond the video are errors.
    """
    if video_s <= 0:
        raise NarrationError("The video has no duration.")
    if not segments:
        text = (script or "").strip()
        if not text:
            raise NarrationError("Write the narration text first.")
        return [{"id": "seg_0", "text": text, "start": 0.0, "end": float(video_s)}]
    if len(segments) > MAX_SEGMENTS:
        raise NarrationError(f"At most {MAX_SEGMENTS} segments.")
    rows = []
    for i, seg in enumerate(segments):
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(seg.get("start", 0.0))
            end = seg.get("end")
            end = None if end in (None, "") else float(end)
        except (TypeError, ValueError):
            raise NarrationError(f"Segment {i + 1} has an invalid time.")
        if start < 0 or start >= video_s:
            raise NarrationError(f"Segment {i + 1} starts outside the video ({start:.1f}s).")
        rows.append({"text": text, "start": start, "end": end})
    if not rows:
        raise NarrationError("Write the narration text first.")
    rows.sort(key=lambda r: r["start"])
    for i, row in enumerate(rows):
        limit = rows[i + 1]["start"] if i + 1 < len(rows) else float(video_s)
        if row["end"] is None:
            row["end"] = limit
        row["end"] = min(row["end"], float(video_s))
        if row["end"] > limit + 1e-6:
            raise NarrationError(f"Segment {i + 1} overlaps the next one.")
        if row["end"] - row["start"] < MIN_SLOT_S:
            raise NarrationError(f"Segment {i + 1} is shorter than {MIN_SLOT_S:g}s.")
        row["id"] = f"seg_{i}"
    return rows


def plan_timing(segs: list[dict], natural_durs: list[float], video_s: float) -> FitPlan:
    return plan_fit(segs, natural_durs, video_s, FIT_PARAMS)


def _fade_out(clip: np.ndarray, sr: int) -> np.ndarray:
    n = min(len(clip), int(FADE_S * sr))
    if n > 0:
        clip = clip.copy()
        clip[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
    return clip


async def _resample(wav: np.ndarray, sr: int) -> np.ndarray:
    if sr == SAMPLE_RATE:
        return wav
    import torch
    import torchaudio

    t = torch.from_numpy(wav).unsqueeze(0)
    return torchaudio.functional.resample(t, sr, SAMPLE_RATE)[0].numpy()


async def _stretch(wav: np.ndarray, rate: float) -> np.ndarray:
    """Pitch-preserving rate change (>1 faster); falls back to the input on ffmpeg failure."""
    from services.ffmpeg_utils import _pitch_preserving_stretch

    import torch

    target = max(1, int(round(len(wav) / rate)))
    try:
        out = await _pitch_preserving_stretch(torch.from_numpy(wav).unsqueeze(0), target, SAMPLE_RATE)
        return out[0].numpy().astype(np.float32, copy=False)
    except Exception as exc:  # noqa: BLE001 - keep the take, unfitted
        logger.warning("narration stretch failed (%s); using the unstretched clip", exc)
        return wav


async def render_track(
    segs: list[dict],
    synth: Synth,
    video_s: float,
    *,
    progress: Optional[Callable[[int, int], None]] = None,
) -> "tuple[np.ndarray, dict]":
    """Synthesize every segment, fit it to its slot and lay it on a silent timeline."""
    clips: list[np.ndarray] = []
    for i, seg in enumerate(segs):
        wav, sr = await synth(seg["text"])
        wav = np.asarray(wav, dtype=np.float32).reshape(-1)
        clips.append(await _resample(wav, sr))
        if progress:
            progress(i + 1, len(segs))
    plan = plan_timing(segs, [len(c) / SAMPLE_RATE for c in clips], video_s)
    track = np.zeros(int(round(video_s * SAMPLE_RATE)), dtype=np.float32)
    report = []
    for seg, fit, clip in zip(segs, plan.segments, clips):
        if abs(fit.audio_rate - 1.0) > 0.01:
            clip = await _stretch(clip, fit.audio_rate)
        start = int(round(seg["start"] * SAMPLE_RATE))
        room = int(round((fit.effective_end - seg["start"]) * SAMPLE_RATE))
        trimmed = len(clip) > room
        if trimmed:
            clip = _fade_out(clip[:room], SAMPLE_RATE)
        end = min(start + len(clip), len(track))
        track[start:end] += clip[: end - start]
        report.append({
            "id": seg["id"], "start": seg["start"], "end": seg["end"],
            "audio_rate": fit.audio_rate, "status": fit.status,
            "natural_s": round(len(clips[len(report)]) / SAMPLE_RATE, 2),
            "trimmed": trimmed, "overflow_s": fit.overflow_s,
        })
    peak = float(np.max(np.abs(track))) if track.size else 0.0
    if peak > 0.98:
        track *= 0.98 / peak
    return track, {"segments": report, "needs_trim": any(r["trimmed"] for r in report)}


def write_wav(path: str, track: np.ndarray) -> None:
    import soundfile as sf

    sf.write(path, track, SAMPLE_RATE, subtype="PCM_16")


def mux_command(
    ffmpeg: str, video: str, narration: str, out: str, *,
    keep_original: bool, has_original_audio: bool, original_volume: float = 0.25,
) -> list[str]:
    """argv that puts the narration on the video (video stream copied, not re-encoded).

    ``keep_original`` mixes the original soundtrack under the narration at
    ``original_volume`` (0-1); otherwise the original audio is dropped.
    """
    from services.ffmpeg_utils import local_inputs_only

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", video, "-i", narration]
    if keep_original and has_original_audio:
        gain = min(max(float(original_volume), 0.0), 1.0)
        graph = (
            f"[0:a]aformat=channel_layouts=stereo,volume={gain:g}[bg];"
            "[1:a]aformat=channel_layouts=stereo[vo];"
            "[bg][vo]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.98[aout]"
        )
        cmd += ["-filter_complex", graph, "-map", "0:v:0", "-map", "[aout]"]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    if out.lower().endswith((".mp4", ".mov", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    cmd.append(out)
    return local_inputs_only(cmd, "ffmpeg")


async def mux(video: str, narration: str, out: str, *, keep_original: bool,
              original_volume: float = 0.25) -> None:
    from services.ffmpeg_utils import find_ffmpeg, has_audio_stream, spawn_subprocess

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise NarrationError("ffmpeg is not available. Install it from Settings -> Models.")
    has_audio = bool(await asyncio.to_thread(has_audio_stream, video))
    cmd = mux_command(ffmpeg, video, narration, out, keep_original=keep_original,
                      has_original_audio=has_audio, original_volume=original_volume)
    proc = await spawn_subprocess(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0 or not os.path.isfile(out):
        raise NarrationError("Could not write the video: " + (err.decode(errors="replace")[-300:] or "ffmpeg failed"))
