"""alert_event notified_at (deliver each alert once)

Revision ID: a1b2c3d4e5f6
Revises: c309a4097dc4
Create Date: 2026-08-24 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "c309a4097dc4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alert_events",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: treat every already-existing event as already delivered (it was sent in
    # prior daily digests). Otherwise the first digest after deploy would re-send the
    # whole active backlog once. New events start NULL and notify exactly once.
    op.execute("UPDATE alert_events SET notified_at = fired_at WHERE notified_at IS NULL")


def downgrade() -> None:
    op.drop_column("alert_events", "notified_at")
