"""people directory, alert routes, alert workflow + activity timeline (T97)

Revision ID: b7d2e9a41c35
Revises: a1b2c3d4e5f6
Create Date: 2026-09-28 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b7d2e9a41c35"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(length=8), nullable=False, server_default="person"),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("handle", sa.String(length=64), nullable=False),
        sa.Column("discord_id", sa.String(length=32), nullable=True),
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_people_user_id_users"),
            nullable=True,
        ),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", name="uq_people_user_id"),
    )
    op.create_index("ix_people_handle", "people", ["handle"], unique=True)

    op.create_table(
        "alert_routes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "company_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "companies.id", ondelete="CASCADE", name="fk_alert_routes_company_id_companies"
            ),
            nullable=True,
        ),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "projects.id", ondelete="CASCADE", name="fk_alert_routes_project_id_projects"
            ),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="CASCADE", name="fk_alert_routes_person_id_people"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_alert_routes_person_id", "alert_routes", ["person_id"])
    op.create_index(
        "uq_alert_route_scope_kind",
        "alert_routes",
        [sa.text("coalesce(company_id, 0)"), sa.text("coalesce(project_id, 0)"), "kind"],
        unique=True,
    )

    op.add_column("alert_events", sa.Column("assignee_person_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "alert_events", sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("alert_events", sa.Column("acked_by_id", sa.BigInteger(), nullable=True))
    op.add_column("alert_events", sa.Column("resolved_by_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "alert_events", sa.Column("resolution_note", sa.String(length=500), nullable=True)
    )
    op.create_foreign_key(
        "fk_alert_events_assignee_person_id_people",
        "alert_events",
        "people",
        ["assignee_person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_alert_events_acked_by_id_users",
        "alert_events",
        "users",
        ["acked_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_alert_events_resolved_by_id_users",
        "alert_events",
        "users",
        ["resolved_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_alert_events_assignee_person_id", "alert_events", ["assignee_person_id"]
    )

    op.create_table(
        "alert_activity",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "alert_event_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "alert_events.id",
                ondelete="CASCADE",
                name="fk_alert_activity_alert_event_id_alert_events",
            ),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_alert_activity_actor_user_id_users"
            ),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("data_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_alert_activity_alert_event_id", "alert_activity", ["alert_event_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_activity_alert_event_id", table_name="alert_activity")
    op.drop_table("alert_activity")

    op.drop_index("ix_alert_events_assignee_person_id", table_name="alert_events")
    op.drop_constraint(
        "fk_alert_events_resolved_by_id_users", "alert_events", type_="foreignkey"
    )
    op.drop_constraint("fk_alert_events_acked_by_id_users", "alert_events", type_="foreignkey")
    op.drop_constraint(
        "fk_alert_events_assignee_person_id_people", "alert_events", type_="foreignkey"
    )
    op.drop_column("alert_events", "resolution_note")
    op.drop_column("alert_events", "resolved_by_id")
    op.drop_column("alert_events", "acked_by_id")
    op.drop_column("alert_events", "acked_at")
    op.drop_column("alert_events", "assignee_person_id")

    op.drop_index("uq_alert_route_scope_kind", table_name="alert_routes")
    op.drop_index("ix_alert_routes_person_id", table_name="alert_routes")
    op.drop_table("alert_routes")

    op.drop_index("ix_people_handle", table_name="people")
    op.drop_table("people")
