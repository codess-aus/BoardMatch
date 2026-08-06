"""Core repository tables: candidates, board opportunities, applications,
application events, and fit evaluations.

Revision ID: 0002_core_repository_tables
Revises: 0001_baseline
Create Date: 2025-01-02

Provides durable Postgres-compatible parity for the repositories previously
implemented only in-memory in
``boardmatch.infrastructure.repositories.memory``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_core_repository_tables"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("headline", sa.String(), nullable=False, server_default=""),
        sa.Column("years_experience", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("sectors", sa.JSON(), nullable=False),
        sa.Column("credentials", sa.JSON(), nullable=False),
        sa.Column("board_experience", sa.JSON(), nullable=False),
        sa.Column("achievements", sa.JSON(), nullable=False),
        sa.Column("locations", sa.JSON(), nullable=False),
        sa.Column("connections", sa.JSON(), nullable=False),
    )

    op.create_table(
        "board_opportunities",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("organisation", sa.String(), nullable=False),
        sa.Column("sector", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("remuneration", sa.String(), nullable=False),
        sa.Column("fee_aud", sa.Integer(), nullable=True),
        sa.Column("closes_on", sa.String(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("desirable_skills", sa.JSON(), nullable=False),
    )

    op.create_table(
        "applications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("opportunity_id", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])

    op.create_table(
        "application_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "application_id",
            sa.String(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_stage", sa.String(), nullable=False),
        sa.Column("new_stage", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_application_events_user_id", "application_events", ["user_id"])

    op.create_table(
        "fit_evaluations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("opportunity_id", sa.String(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("scoring_version", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(), nullable=False),
        sa.Column("matched_skills", sa.JSON(), nullable=False),
        sa.Column("missing_skills", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.JSON(), nullable=False),
        sa.Column("gap_actions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fit_evaluations_user_id", "fit_evaluations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_fit_evaluations_user_id", table_name="fit_evaluations")
    op.drop_table("fit_evaluations")
    op.drop_index("ix_application_events_user_id", table_name="application_events")
    op.drop_table("application_events")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_table("applications")
    op.drop_table("board_opportunities")
    op.drop_table("candidates")
