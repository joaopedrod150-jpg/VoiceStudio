"""Several recordings of one voice -> one saved clone profile.

The source recordings are kept (``voice_profile_refs``) and merged by
``services.voice_reference`` into the single reference clip every engine
consumes (``voice_profiles.ref_audio_path``). Old one-clip profiles have no
rows and keep working unchanged; the first time one gains an extra recording
its current clip is registered as source #1.

Endpoints:
    GET    /profiles/{id}/references            sources + merged duration
    POST   /profiles/{id}/references            add recordings, rebuild
    DELETE /profiles/{id}/references/{ref_id}   drop one, rebuild
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile

from api.routers import profiles as _p
from core import event_bus
from core.db import db_conn

logger = logging.getLogger("omnivoice.profile_references")
router = APIRouter()

MAX_SOURCES = 20


def _engine_budget_s() -> float:
    """Seconds of merged reference: inside every engine's limit (OmniVoice 20 s)."""
    from services.voice_reference import DEFAULT_MAX_SECONDS

    return DEFAULT_MAX_SECONDS


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail="That voice profile doesn't exist. It may have been deleted from another tab.",
    )


async def save_sources(profile_id: str, uploads: List[UploadFile]) -> List[dict]:
    """Write uploads to voices/ as source files; returns row dicts (not yet in DB)."""
    rows: List[dict] = []
    os.makedirs(_p.VOICES_DIR, exist_ok=True)
    try:
        for upload in uploads:
            ext = os.path.splitext(upload.filename or "")[1].lower()
            if ext not in _p._REF_AUDIO_EXTS:
                raise HTTPException(
                    status_code=415,
                    detail="Choose supported audio files: WAV, MP3, M4A, FLAC, OGG, Opus, AAC, or WebM.",
                )
            ref_id = uuid.uuid4().hex[:8]
            filename = f"{profile_id}-src-{ref_id}{ext}"
            path = os.path.join(_p.VOICES_DIR, filename)
            if await _p._save_upload(upload, path) < _p._MIN_REF_AUDIO_BYTES:
                with contextlib.suppress(OSError):
                    os.remove(path)
                raise HTTPException(status_code=422, detail=f"{upload.filename}: recording is empty or too short.")
            rows.append({
                "id": ref_id, "filename": filename,
                "original_name": os.path.basename(upload.filename or filename),
            })
    except BaseException:
        for row in rows:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(_p.VOICES_DIR, row["filename"]))
        raise
    return rows


def _insert_rows(conn, profile_id: str, rows: List[dict], start: int) -> None:
    for i, row in enumerate(rows):
        conn.execute(
            "INSERT INTO voice_profile_refs (id, profile_id, filename, original_name, "
            "duration, status, reason, position, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (row["id"], profile_id, row["filename"], row["original_name"],
             row.get("duration", 0.0), row.get("status", "ok"), row.get("reason", ""),
             start + i, time.time()),
        )


async def merge_sources(rows: List[dict], out_path: str) -> dict:
    """Merge source rows into ``out_path``; annotates rows with status/duration."""
    from services.voice_reference import ReferenceRejected, build_reference

    paths = [os.path.join(_p.VOICES_DIR, r["filename"]) for r in rows]
    try:
        report = await asyncio.to_thread(
            build_reference, paths, out_path, max_seconds=_engine_budget_s()
        )
    except ReferenceRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    for row, src in zip(rows, report["sources"]):
        row["status"], row["duration"] = src["status"], src.get("duration", 0.0)
        row["reason"] = src.get("reason", "")
    return report


def cleanup_sources(profile_id: str, conn) -> List[str]:
    """Delete refs rows for a profile; returns the source filenames to remove."""
    names = [r["filename"] for r in conn.execute(
        "SELECT filename FROM voice_profile_refs WHERE profile_id=?", (profile_id,))]
    conn.execute("DELETE FROM voice_profile_refs WHERE profile_id=?", (profile_id,))
    return names


def _list(conn, profile_id: str) -> List[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM voice_profile_refs WHERE profile_id=? ORDER BY position, created_at",
        (profile_id,)).fetchall()]


def _summary(profile_id: str) -> dict:
    with db_conn() as conn:
        refs = _list(conn, profile_id)
    return {
        "references": [
            {k: r[k] for k in ("id", "original_name", "duration", "status", "reason", "position")}
            for r in refs
        ],
        "source_seconds": round(sum(r["duration"] or 0.0 for r in refs if r["status"] == "ok"), 2),
    }


async def _rebuild(profile_id: str, row, rows: List[dict], *, insert_from: Optional[int] = None,
                   drop_id: Optional[str] = None) -> dict:
    """Merge ``rows`` into a new versioned clip, commit the swap, retire old files."""
    new_filename = f"{profile_id}-{uuid.uuid4().hex[:8]}.wav"
    new_path = _p._voices_path(new_filename)
    os.makedirs(_p.VOICES_DIR, exist_ok=True)
    try:
        report = await merge_sources(rows, new_path)
        text = await _p._auto_transcribe_reference(new_path)
        with db_conn() as conn:
            cur = conn.execute(
                _p._REPLACE_AUDIO_SQL,
                (new_filename, text, None, None, None, None, profile_id, row["ref_audio_path"]),
            )
            if cur.rowcount == 0:
                raise HTTPException(
                    status_code=409,
                    detail="This voice changed while the recordings were processing. Reopen it and try again.",
                )
            if drop_id:
                conn.execute("DELETE FROM voice_profile_refs WHERE id=?", (drop_id,))
            if insert_from is not None:
                _insert_rows(conn, profile_id, [r for r in rows if r.get("new")], insert_from)
            for r in rows:
                if not r.get("new"):
                    conn.execute(
                        "UPDATE voice_profile_refs SET duration=?, status=?, reason=? WHERE id=?",
                        (r["duration"], r["status"], r["reason"], r["id"]),
                    )
            updated = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(new_path)
        raise
    for column in ("ref_audio_path", "locked_audio_path"):
        _p._retire_voice_file(row[column], keep=new_filename)
    _p._remove_voice_file(row["consent_audio_path"], keep=new_filename)
    await _p._sweep_retired_off_loop()
    event_bus.emit("profiles", {"action": "updated", "id": profile_id})
    out = _p._profile_record(updated)
    out.update(_summary(profile_id))
    out["merged_seconds"] = report["duration"]
    return out


def _load_profile(profile_id: str):
    if not _p._PROFILE_ID_RE.fullmatch(profile_id or ""):
        raise _not_found()
    with db_conn() as conn:
        row = conn.execute(
            "SELECT kind, ref_audio_path, locked_audio_path, consent_audio_path "
            "FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
        refs = _list(conn, profile_id) if row else []
    if not row:
        raise _not_found()
    if (row["kind"] or "clone") != "clone":
        raise HTTPException(
            status_code=409,
            detail="Designed voices are defined by their recipe, not recordings.",
        )
    return row, refs


def _legacy_row(profile_id: str, row) -> Optional[dict]:
    """The profile's current single clip, as a source row (copied so the merged
    clip's later retirement cannot take the source with it)."""
    src = _p._voices_path(row["ref_audio_path"]) if row["ref_audio_path"] else None
    if not src or not os.path.isfile(src):
        return None
    ref_id = uuid.uuid4().hex[:8]
    ext = os.path.splitext(src)[1]
    filename = f"{profile_id}-src-{ref_id}{ext}"
    shutil.copyfile(src, os.path.join(_p.VOICES_DIR, filename))
    return {"id": ref_id, "filename": filename, "original_name": row["ref_audio_path"], "new": True}


@router.get("/profiles/{profile_id}/references")
def list_references(profile_id: str):
    _load_profile(profile_id)
    return _summary(profile_id)


@router.post("/profiles/{profile_id}/references")
async def add_references(profile_id: str, ref_audios: List[UploadFile] = File(...)):
    row, refs = _load_profile(profile_id)
    if len(refs) + len(ref_audios) > MAX_SOURCES:
        raise HTTPException(status_code=422, detail=f"A voice can have at most {MAX_SOURCES} recordings.")
    lock = _p._replace_locks.get(profile_id)
    if lock is None:
        lock = _p._replace_locks[profile_id] = asyncio.Lock()
    async with lock:
        row, refs = _load_profile(profile_id)
        new_rows = await save_sources(profile_id, ref_audios)
        for r in new_rows:
            r["new"] = True
        seed = []
        if not refs:
            legacy = _legacy_row(profile_id, row)
            if legacy:
                seed = [legacy]
        for r in refs:
            r["new"] = False
        rows = refs + seed + new_rows
        try:
            return await _rebuild(profile_id, row, rows, insert_from=len(refs))
        except BaseException:
            for r in seed + new_rows:
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(_p.VOICES_DIR, r["filename"]))
            raise


@router.delete("/profiles/{profile_id}/references/{ref_id}")
async def delete_reference(profile_id: str, ref_id: str):
    row, refs = _load_profile(profile_id)
    target = next((r for r in refs if r["id"] == ref_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="That recording isn't part of this voice.")
    if len(refs) <= 1:
        raise HTTPException(
            status_code=409,
            detail="A voice needs at least one recording. Replace it instead of removing it.",
        )
    lock = _p._replace_locks.get(profile_id)
    if lock is None:
        lock = _p._replace_locks[profile_id] = asyncio.Lock()
    async with lock:
        row, refs = _load_profile(profile_id)
        rest = [dict(r, new=False) for r in refs if r["id"] != ref_id]
        out = await _rebuild(profile_id, row, rest, drop_id=ref_id)
    with contextlib.suppress(OSError):
        os.remove(os.path.join(_p.VOICES_DIR, target["filename"]))
    return out
