"""Explicit synthetic setup/reset. Never called by normal server startup."""

import argparse
import sqlite3
from pathlib import Path

FIXTURE_ORGANIZATIONS = [
    ("udaan", "Udaan Foundation"),
    ("asha", "Asha Trust"),
]

FIXTURE_STAFF = [
    ("udaan_case_worker", "udaan", "case_worker"),
    ("udaan_program_manager", "udaan", "program_manager"),
    ("udaan_finance", "udaan", "finance"),
    ("asha_case_worker", "asha", "case_worker"),
    ("asha_program_manager", "asha", "program_manager"),
    ("asha_finance", "asha", "finance"),
]

FIXTURE_BENEFICIARIES = [
    ("udaan_ananya", "udaan", "Ananya Sharma"),
    ("udaan_rohan", "udaan", "Rohan Kumar"),
    ("asha_kavya", "asha", "Kavya Iyer"),
    ("asha_arjun", "asha", "Arjun Singh"),
]

# Allocations are USD cents: 1,000,000 cents = USD 10,000.00.
FIXTURE_FUNDS = [
    ("udaan_emergency", "udaan", "Emergency Assistance", 1_000_000),
    ("udaan_transport", "udaan", "Transportation", 50_000),
    ("asha_emergency", "asha", "Emergency Assistance", 1_000_000),
    ("asha_transport", "asha", "Transportation", 50_000),
]


def setup(path: Path, *, reset: bool = False) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not reset:
            raise ValueError("Database exists; pass --reset to replace fixture data (stop servers first)")
        path.unlink()
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(Path(__file__).with_name("database_schema.sql").read_text())
        db.executemany("INSERT INTO organizations(organization_id, name) VALUES (?,?)", FIXTURE_ORGANIZATIONS)
        db.executemany("INSERT INTO staff(staff_id, organization_id, role) VALUES (?,?,?)", FIXTURE_STAFF)
        db.executemany("INSERT INTO beneficiaries(beneficiary_id, organization_id, name) VALUES (?,?,?)", FIXTURE_BENEFICIARIES)
        db.executemany("INSERT INTO funds(fund_id, organization_id, name, allocation_cents) VALUES (?,?,?,?)", FIXTURE_FUNDS)
        db.commit()
        path.chmod(0o600)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/assistance.sqlite3"))
    parser.add_argument("--reset", action="store_true", help="Destructively replace synthetic data; stop servers first")
    args = parser.parse_args()
    setup(args.db, reset=args.reset)
    print(f"Synthetic database ready: {args.db.resolve()}")


if __name__ == "__main__":
    main()
