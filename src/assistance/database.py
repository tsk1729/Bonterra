"""Local SQLite lifecycle and trusted fixture resolution (R01/R08/R10)."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from assistance.domain_models import StaffIdentity
from assistance.sql_queries import CHECK_SCHEMA_VERSION, FIND_STAFF_IDENTITY


def connect(path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    # mode=rw prevents startup or a typo from silently creating a database.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=timeout, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def transaction(path: Path, *, write: bool = False, timeout: float = 5.0):
    db = connect(path, timeout=timeout)
    try:
        if write:
            db.execute("BEGIN IMMEDIATE")
        else:
            db.execute("BEGIN")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def validate_schema_version(path: Path) -> None:
    with transaction(path) as db:
        if db.execute(CHECK_SCHEMA_VERSION).fetchone()[0] != 8:
            raise ValueError("Unsupported database version; run explicit setup")


def resolve_staff_identity(path: Path, identity: str | None) -> StaffIdentity:
    if not identity:
        raise ValueError("ASSISTANCE_IDENTITY is required")
    with transaction(path) as db:
        row = db.execute(FIND_STAFF_IDENTITY, (identity,)).fetchone()
    if row is None:
        raise ValueError("Unknown fixture identity")
    return StaffIdentity(
        staff_id=row["staff_id"],
        organization_id=row["organization_id"],
        role=row["role"],
    )
