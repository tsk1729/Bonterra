from pathlib import Path

import pytest

from assistance.domain_models import CreateRequest
from assistance.seed_database import setup
from assistance.funding_service import FundingService
from assistance.database import resolve_staff_identity


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "test.sqlite3"
    setup(path)
    return path


def funding_service(
    path: Path,
    role: str = "case_worker",
    organization_id: str = "udaan",
) -> FundingService:
    identity = resolve_staff_identity(path, f"{organization_id}_{role}")
    return FundingService(path, identity)


def payload(**changes):
    values = {
        "beneficiary_id": "udaan_ananya",
        "fund_id": "udaan_emergency",
        "purpose": "Rent",
        "amount_cents": 30_000,
        "idempotency_key": "rent_1",
    }
    values.update(changes)
    return CreateRequest(**values)
