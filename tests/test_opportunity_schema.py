from __future__ import annotations

import sqlite3
from decimal import Decimal
from uuid import uuid4

import pytest

from boardmatch.infrastructure.db.migrations import apply_migrations


def _id() -> str:
    return str(uuid4())


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    apply_migrations(connection)
    try:
        yield connection
    finally:
        connection.close()


def _create_source(db: sqlite3.Connection, source_key: str = "gov") -> str:
    source_id = _id()
    db.execute(
        """
        INSERT INTO ingestion_sources (id, source_key, name, source_url)
        VALUES (?, ?, ?, ?)
        """,
        (
            source_id,
            source_key,
            "Government board vacancies",
            "https://example.gov/jobs",
        ),
    )
    return source_id


def _create_opportunity(
    db: sqlite3.Connection,
    *,
    status: str = "active",
    remuneration: str = "paid",
    fee_amount: Decimal | None = Decimal("125000.50"),
    fee_currency: str | None = "AUD",
) -> str:
    opportunity_id = _id()
    db.execute(
        """
        INSERT INTO opportunities (
            id, title, organisation, sector, location, status,
            remuneration, fee_amount, fee_currency
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            opportunity_id,
            "Non-executive director",
            "Example Health",
            "Health",
            "Sydney",
            status,
            remuneration,
            str(fee_amount) if fee_amount is not None else None,
            fee_currency,
        ),
    )
    return opportunity_id


def test_opportunity_creation_and_retrieval_with_decimal_fee(db: sqlite3.Connection):
    opportunity_id = _create_opportunity(db)

    row = db.execute(
        "SELECT title, status, remuneration, fee_amount, fee_currency FROM opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()

    assert row["title"] == "Non-executive director"
    assert row["status"] == "active"
    assert row["remuneration"] == "paid"
    assert Decimal(str(row["fee_amount"])) == Decimal("125000.50")
    assert row["fee_currency"] == "AUD"


def test_multiple_sources_can_link_to_one_opportunity(db: sqlite3.Connection):
    opportunity_id = _create_opportunity(db)
    gov_source_id = _create_source(db, "gov")
    aicd_source_id = _create_source(db, "aicd")

    for source_id, external_id in (
        (gov_source_id, "GOV-123"),
        (aicd_source_id, "AICD-987"),
    ):
        db.execute(
            """
            INSERT INTO opportunity_source_records (
                id, opportunity_id, source_id, external_id, source_url, title, organisation, observed_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _id(),
                opportunity_id,
                source_id,
                external_id,
                f"https://example.org/{external_id}",
                "Non-executive director",
                "Example Health",
                "active",
            ),
        )

    count = db.execute(
        "SELECT COUNT(*) FROM opportunity_source_records WHERE opportunity_id = ?",
        (opportunity_id,),
    ).fetchone()[0]

    assert count == 2


def test_duplicate_external_source_record_is_rejected(db: sqlite3.Connection):
    opportunity_id = _create_opportunity(db)
    source_id = _create_source(db)
    values = (
        _id(),
        opportunity_id,
        source_id,
        "GOV-123",
        "https://example.gov/jobs/GOV-123",
        "Non-executive director",
        "Example Health",
    )
    db.execute(
        """
        INSERT INTO opportunity_source_records (
            id, opportunity_id, source_id, external_id, source_url, title, organisation
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO opportunity_source_records (
                id, opportunity_id, source_id, external_id, source_url, title, organisation
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_id(), *values[1:]),
        )


def test_expiry_status_update_retains_historical_source_records(db: sqlite3.Connection):
    opportunity_id = _create_opportunity(db)
    source_id = _create_source(db)
    db.execute(
        """
        INSERT INTO opportunity_source_records (
            id, opportunity_id, source_id, external_id, source_url, title, organisation
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _id(),
            opportunity_id,
            source_id,
            "GOV-123",
            "https://example.gov/jobs/GOV-123",
            "Non-executive director",
            "Example Health",
        ),
    )

    db.execute(
        "UPDATE opportunities SET status = 'expired' WHERE id = ?", (opportunity_id,)
    )

    row = db.execute(
        """
        SELECT o.status, sr.external_id
        FROM opportunities o
        JOIN opportunity_source_records sr ON sr.opportunity_id = o.id
        WHERE o.id = ?
        """,
        (opportunity_id,),
    ).fetchone()
    assert row["status"] == "expired"
    assert row["external_id"] == "GOV-123"


def test_remuneration_and_missing_fee_validation(db: sqlite3.Connection):
    paid_without_fee = _create_opportunity(
        db, remuneration="paid", fee_amount=None, fee_currency=None
    )
    voluntary = _create_opportunity(
        db, remuneration="voluntary", fee_amount=None, fee_currency=None
    )
    unknown = _create_opportunity(
        db, remuneration="unknown", fee_amount=None, fee_currency=None
    )

    rows = db.execute(
        "SELECT id, remuneration, fee_amount, fee_currency FROM opportunities"
    ).fetchall()
    assert {row["id"] for row in rows} >= {paid_without_fee, voluntary, unknown}
    assert all(row["fee_amount"] is None for row in rows)
    assert all(row["fee_currency"] is None for row in rows)

    with pytest.raises(sqlite3.IntegrityError):
        _create_opportunity(
            db,
            remuneration="voluntary",
            fee_amount=Decimal("1000.00"),
            fee_currency="AUD",
        )

    with pytest.raises(sqlite3.IntegrityError):
        _create_opportunity(
            db, remuneration="paid", fee_amount=Decimal("1000.00"), fee_currency=None
        )


def test_verification_timestamps_and_statuses_are_stored(db: sqlite3.Connection):
    opportunity_id = _create_opportunity(db, status="unverified")
    source_id = _create_source(db)
    source_record_id = _id()
    db.execute(
        """
        INSERT INTO opportunity_source_records (
            id, opportunity_id, source_id, external_id, source_url, title, organisation
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_record_id,
            opportunity_id,
            source_id,
            "GOV-123",
            "https://example.gov/jobs/GOV-123",
            "Non-executive director",
            "Example Health",
        ),
    )
    verified_at = "2026-08-05T02:30:00Z"

    db.execute(
        """
        INSERT INTO opportunity_verifications (
            id, opportunity_id, source_record_id, verification_status, verified_at, verified_by
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (_id(), opportunity_id, source_record_id, "verified", verified_at, "scheduler"),
    )

    row = db.execute(
        """
        SELECT verification_status, verified_at, last_checked_at, verified_by
        FROM opportunity_verifications
        WHERE opportunity_id = ?
        """,
        (opportunity_id,),
    ).fetchone()

    assert row["verification_status"] == "verified"
    assert row["verified_at"] == verified_at
    assert row["last_checked_at"]
    assert row["verified_by"] == "scheduler"


def test_migration_downgrade_removes_schema():
    connection = sqlite3.connect(":memory:")
    apply_migrations(connection)
    apply_migrations(connection, direction="down")

    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()

    assert tables == []
