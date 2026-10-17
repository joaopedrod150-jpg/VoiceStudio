"""Multi-reference voice profiles.

Zero-shot cloning engines (OmniVoice and the others VoiceStudio ships) take ONE
reference clip of roughly 3-20 s; none accepts several clips or a speaker
embedding. So several recordings of one person are turned into one better
clip: each source is decoded to 24 kHz mono, level-normalised, trimmed of
silence and split into speech segments; the best segments are then picked
round-robin across the sources (so every recording contributes) until the
engine's duration budget is full, and joined with short pauses.

The merged clip is stored exactly like a single-clip profile's reference, so
every engine and every older profile keeps working unchanged.
"""
from __future__ import annotations

import logging
import os
from typing import Sequence

import numpy as np

logger = logging.getLogger("omnivoice.voice_reference")

TARGET_SR = 24000
MIN_CLIP_S = 1.0  # a whole clip shorter than this is not a usable sample
MIN_SEGMENT_S = 1.5  # speech segments shorter than this are dropped
MAX_SEGMENT_S = 10.0
DEFAULT_MAX_SECONDS = 15.0  # inside every engine's limit (OmniVoice: 20 s)
GAP_S = 0.12
TARGET_RMS = 0.1  # about -20 dBFS
PEAK_LIMIT = 0.97
FRAME_S = 0.03
MIN_SPEECH_RMS = 0.002  # below this a clip is treated as silence/noise floor
MAX_CLIPPED_RATIO = 0.02


class ReferenceRejected(ValueError):
    """A source clip (or the whole set) cannot be used as a voice reference."""


def load_mono(path: str) -> np.ndarray:
    """Decode any supported audio file to float32 mono at TARGET_SR."""
    import torch
    import torchaudio

    from services.audio_io import load_audio

    try:
        wav, sr = load_audio(path)
    except Exception as exc:
        raise ReferenceRejected(f"could not be read as audio ({exc})") from exc
    mono = wav.mean(dim=0, keepdim=True) if wav.shape[0] > 1 else wav
    if sr != TARGET_SR:
        mono = torchaudio.functional.resample(mono, sr, TARGET_SR)
    return torch.clamp(mono[0], -1.0, 1.0).numpy().astype(np.float32, copy=False)


def _frame_rms(x: np.ndarray) -> np.ndarray:
    n = int(FRAME_S * TARGET_SR)
    count = len(x) // n
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    frames = x[: count * n].reshape(count, n)
    return np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)).astype(np.float32)


def _speech_mask(x: np.ndarray) -> np.ndarray:
    """Per-frame voice-activity flags from an adaptive energy threshold."""
    rms = _frame_rms(x)
    if rms.size == 0:
        return np.zeros(0, dtype=bool)
    floor = float(np.percentile(rms, 10))
    loud = float(np.percentile(rms, 95))
    if loud < MIN_SPEECH_RMS:
        return np.zeros(rms.size, dtype=bool)
    threshold = max(floor * 3.0, loud * 0.1, MIN_SPEECH_RMS)
    return rms > threshold


def split_speech(
    x: np.ndarray, *, max_gap_s: float = 0.35, max_segment_s: float = MAX_SEGMENT_S
) -> list[tuple[int, int]]:
    """Sample spans of continuous speech, at least MIN_SEGMENT_S long.

    Pauses up to ``max_gap_s`` stay inside a span so a sentence is not cut
    mid-breath; spans longer than ``max_segment_s`` are cut at their quietest frame.
    """
    mask = _speech_mask(x)
    if not mask.any():
        return []
    n = int(FRAME_S * TARGET_SR)
    rms = _frame_rms(x)
    max_gap = int(max_gap_s / FRAME_S)
    spans: list[list[int]] = []
    for i in np.flatnonzero(mask):
        if spans and i - spans[-1][1] <= max_gap + 1:
            spans[-1][1] = int(i)
        else:
            spans.append([int(i), int(i)])
    out: list[tuple[int, int]] = []
    max_frames = max(2, int(max_segment_s / FRAME_S))
    for first, last in spans:
        while last - first + 1 > max_frames:
            lo, hi = first + max_frames // 2, first + max_frames
            cut = lo + int(np.argmin(rms[lo:hi]))
            out.append((first * n, (cut + 1) * n))
            first = cut + 1
        out.append((first * n, (last + 1) * n))
    pad = int(0.05 * TARGET_SR)
    return [
        (max(0, s - pad), min(len(x), e + pad))
        for s, e in out
        if (e - s) / TARGET_SR >= MIN_SEGMENT_S
    ]


def clean_clip(raw: np.ndarray) -> np.ndarray:
    """Remove DC offset, trim edge silence and normalise loudness.

    Raises ReferenceRejected for clips that are too short, silent or clipped.
    """
    x = np.asarray(raw, dtype=np.float32)
    if len(x) / TARGET_SR < MIN_CLIP_S:
        raise ReferenceRejected(f"is shorter than {MIN_CLIP_S:.0f} second")
    x = x - float(np.mean(x))
    if float(np.mean(np.abs(x) >= 0.999)) > MAX_CLIPPED_RATIO:
        raise ReferenceRejected("is heavily clipped (distorted)")
    mask = _speech_mask(x)
    if not mask.any():
        raise ReferenceRejected("contains no detectable speech")
    n = int(FRAME_S * TARGET_SR)
    idx = np.flatnonzero(mask)
    pad = int(0.1 * TARGET_SR)
    x = x[max(0, idx[0] * n - pad): min(len(x), (idx[-1] + 1) * n + pad)]
    if len(x) / TARGET_SR < MIN_CLIP_S:
        raise ReferenceRejected(f"has less than {MIN_CLIP_S:.0f} second of speech")
    sample_mask = np.repeat(_speech_mask(x), n)
    voiced = x[: len(sample_mask)][sample_mask]
    rms = float(np.sqrt(np.mean(voiced.astype(np.float64) ** 2))) if voiced.size else 0.0
    if rms < 1e-6:
        raise ReferenceRejected("contains no detectable speech")
    x = x * min(TARGET_RMS / rms, 50.0)
    peak = float(np.max(np.abs(x)))
    if peak > PEAK_LIMIT:
        x = x * (PEAK_LIMIT / peak)
    return x.astype(np.float32)


def _segment_score(seg: np.ndarray) -> float:
    """Prefer 3-8 s segments; penalise clipping."""
    dur = len(seg) / TARGET_SR
    length_score = 1.0 - min(abs(dur - 5.5) / 5.5, 1.0) * 0.6
    clipped = float(np.mean(np.abs(seg) >= 0.98))
    return length_score - clipped * 10.0


def combine_clips(
    clips: Sequence[np.ndarray], *, max_seconds: float = DEFAULT_MAX_SECONDS
) -> tuple[np.ndarray, dict]:
    """Merge cleaned clips into one reference of at most ``max_seconds``.

    Round-robin: each round every source offers its best remaining segment, so
    all recordings are represented before any one is used twice.
    """
    # A fair share per source: one long recording must not fill the budget.
    share = max(MIN_SEGMENT_S * 2, min(MAX_SEGMENT_S, max_seconds / max(len(clips), 1)))
    queues: list[list[tuple[float, int, np.ndarray]]] = []
    for src, clip in enumerate(clips):
        segs = [clip[s:e] for s, e in split_speech(clip, max_segment_s=share)]
        if not segs and len(clip) / TARGET_SR >= MIN_CLIP_S:
            segs = [clip[: int(share * TARGET_SR)]]
        scored = sorted(
            ((_segment_score(s), i, s) for i, s in enumerate(segs)), key=lambda t: -t[0]
        )
        queues.append(scored)

    budget = int(max_seconds * TARGET_SR)
    gap = int(GAP_S * TARGET_SR)
    picked: list[tuple[int, int, np.ndarray]] = []  # (source, position, audio)
    used = 0
    progressed = True
    while progressed and used < budget:
        progressed = False
        for src, queue in enumerate(queues):
            while queue:
                _, pos, seg = queue[0]
                need = len(seg) + (gap if picked else 0)
                if used + need <= budget:
                    queue.pop(0)
                    picked.append((src, pos, seg))
                    used += need
                    progressed = True
                    break
                queue.pop(0)  # does not fit; try this source's next-best
    if not picked:
        raise ReferenceRejected("no usable speech segments")
    picked.sort(key=lambda t: (t[0], t[1]))
    parts: list[np.ndarray] = []
    for i, (_, _, seg) in enumerate(picked):
        if i:
            parts.append(np.zeros(gap, dtype=np.float32))
        parts.append(seg)
    merged = np.concatenate(parts).astype(np.float32)
    report = {
        "picked": [
            {"source": src, "position": pos, "seconds": round(len(seg) / TARGET_SR, 2)}
            for src, pos, seg in picked
        ]
    }
    return merged, report


def build_reference(
    paths: Sequence[str], out_path: str, *, max_seconds: float = DEFAULT_MAX_SECONDS
) -> dict:
    """Clean every file in ``paths`` and write the merged reference WAV.

    Returns ``{"duration", "sources": [{path, status, duration, reason?}],
    "picked": [...]}``; raises ReferenceRejected if no source is usable.
    """
    import soundfile as sf

    cleaned: list[np.ndarray] = []
    owners: list[int] = []
    sources: list[dict] = []
    for i, path in enumerate(paths):
        entry: dict = {"path": os.path.basename(path)}
        try:
            clip = clean_clip(load_mono(path))
        except ReferenceRejected as exc:
            entry.update(status="rejected", reason=str(exc), duration=0.0)
        else:
            entry.update(status="ok", duration=round(len(clip) / TARGET_SR, 2))
            cleaned.append(clip)
            owners.append(i)
        sources.append(entry)
    if not cleaned:
        reasons = "; ".join(f"{s['path']}: {s.get('reason')}" for s in sources)
        raise ReferenceRejected(f"None of the recordings is usable ({reasons})")
    merged, report = combine_clips(cleaned, max_seconds=max_seconds)
    for p in report["picked"]:
        p["source"] = owners[p["source"]]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    sf.write(out_path, merged, TARGET_SR, subtype="PCM_16")
    return {
        "duration": round(len(merged) / TARGET_SR, 2),
        "sources": sources,
        "picked": report["picked"],
    }
