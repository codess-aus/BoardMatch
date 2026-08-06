"""Extended stores: drafts, documents, network connections, integrations
(+ audit events), account-level audit events, and retention state.

Revision ID: 0003_extended_stores
Revises: 0002_core_repository_tables
Create Date: 2025-01-03

Provides durable Postgres-compatible parity for the modules previously
implemented only in-memory in ``boardmatch.drafts``, ``boardmatch.documents``,
``boardmatch.api.v1.network``, ``boardmatch.integrations``, ``boardmatch.audit``,
and ``boardmatch.retention``.

Note: ``integrations`` deliberately has no column for the live OAuth access
token — only the one-way ``token_hash`` is persisted, preserving the security
property enforced by the in-memory implementation (real access tokens are
held only in process memory and never written to durable storage).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_extended_stores"
down_revision = "0002_core_repository_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drafts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("draft_type", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("engine", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=False, server_default="1.0"),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("opportunity_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_drafts_user_id", "drafts", ["user_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])

    op.create_table(
        "network_connections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("relationship", sa.String(), nullable=False),
        sa.Column("organisations", sa.JSON(), nullable=False),
        sa.Column("board_seats", sa.JSON(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("strength", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_network_connections_user_id", "network_connections", ["user_id"]
    )

    op.create_table(
        "integrations",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # Deliberately no access_token column — real tokens are never persisted.
        sa.Column("token_hash", sa.String(), nullable=True),
    )

    op.create_table(
        "integration_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_integration_audit_events_user_id", "integration_audit_events", ["user_id"]
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=True),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
    )
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])

    op.create_table(
        "extracted_texts",
        sa.Column("document_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extracted_texts_user_id", "extracted_texts", ["user_id"])

    op.create_table(
        "retention_network_records",
        sa.Column("connection_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_retention_network_records_user_id", "retention_network_records", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retention_network_records_user_id",
        table_name="retention_network_records",
    )
    op.drop_table("retention_network_records")
    op.drop_index("ix_extracted_texts_user_id", table_name="extracted_texts")
    op.drop_table("extracted_texts")
    op.drop_index("ix_audit_events_user_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(
        "ix_integration_audit_events_user_id", table_name="integration_audit_events"
    )
    op.drop_table("integration_audit_events")
    op.drop_table("integrations")
    op.drop_index("ix_network_connections_user_id", table_name="network_connections")
    op.drop_table("network_connections")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_drafts_user_id", table_name="drafts")
    op.drop_table("drafts")
