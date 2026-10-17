"""Multi-recording voice profiles: voice_profile_refs

Revision ID: 0014_voice_profile_refs
Revises: 0013_generation_history_design_recipe
Create Date: 2026-10-17 00:00:00.000000

One row per source recording of a clone profile. The merged clip engines read
stays in ``voice_profiles.ref_audio_path``, so profiles without rows (every
profile saved before this revision) keep working unchanged. Additive and
idempotent; mirrored in ``core/db.py::_BASE_SCHEMA``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_voice_profile_refs"
down_revision: Union[str, None] = "0013_generation_history_design_recipe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name}
    ).fetchone()
    return row is not None


def upgrade() -> None:
    if _has_table("voice_profile_refs"):
        return
    op.create_table(
        "voice_profile_refs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("profile_id", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("original_name", sa.Text(), server_default=""),
        sa.Column("duration", sa.Float(), server_default="0"),
        sa.Column("status", sa.Text(), server_default="ok"),
        sa.Column("reason", sa.Text(), server_default=""),
        sa.Column("position", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.Float(), nullable=True),
    )
    op.create_index("idx_voice_profile_refs_profile", "voice_profile_refs", ["profile_id"])


def downgrade() -> None:
    if _has_table("voice_profile_refs"):
        op.drop_index("idx_voice_profile_refs_profile", table_name="voice_profile_refs")
        op.drop_table("voice_profile_refs")
