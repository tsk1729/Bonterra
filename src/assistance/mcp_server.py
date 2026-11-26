"""FastMCP transport boundary. Fixture identity is configuration, never a tool input."""

import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Callable, TypeVar

from fastmcp import FastMCP

from assistance.domain_models import Cents, CreateRequest, Error, Funds, Identifier, Beneficiaries, Purpose, Request, Requests, Result, Status, Summaries
from assistance.funding_service import BusinessError, FundingService, is_busy
from assistance.database import resolve_staff_identity, validate_schema_version

logger = logging.getLogger(__name__)
T = TypeVar("T")


def respond(operation: Callable[[], T]) -> Result[T]:
    try:
        return Result(ok=True, data=operation())
    except BusinessError as exc:
        return Result(ok=False, error=Error(code=exc.code, message=exc.message))
    except sqlite3.OperationalError as exc:
        if is_busy(exc):
            return Result(ok=False, error=Error(code="RETRYABLE_BUSY", message="Database busy; retry the same operation", retryable=True))
        logger.error("Database operation failed (%s)", type(exc).__name__)
    except Exception as exc:
        # No SQL, beneficiary text or stack trace in either protocol result or logs.
        logger.error("Operation failed (%s)", type(exc).__name__)
    return Result(ok=False, error=Error(code="INTERNAL_ERROR", message="Internal operation failed"))


mcp = FastMCP("Udaan Foundation Emergency Assistance", mask_error_details=True, strict_input_validation=True)
# Assigned once at startup, before the server accepts tool calls.
funding_service: FundingService


@mcp.tool
def list_beneficiaries() -> Result[Beneficiaries]:
    """List beneficiaries belonging to the authenticated organization. All staff roles may read."""
    return respond(funding_service.list_beneficiaries)


@mcp.tool
def list_funds() -> Result[Funds]:
    """List the authenticated organization's funds and USD allocations in integer cents."""
    return respond(funding_service.list_funds)


@mcp.tool
def create_request(
    beneficiary_id: Identifier,
    fund_id: Identifier,
    purpose: Purpose,
    amount_cents: Cents,
    idempotency_key: Identifier,
) -> Result[Request]:
    """Case worker: submit immutable Pending request, reserving no money. Amount is positive integer USD cents. Reuse the same idempotency key to return the original request on a retry."""
    payload = CreateRequest(
        beneficiary_id=beneficiary_id,
        fund_id=fund_id,
        purpose=purpose,
        amount_cents=amount_cents,
        idempotency_key=idempotency_key,
    )
    return respond(lambda: funding_service.create_request(payload))


@mcp.tool
def approve_request(request_id: Identifier) -> Result[Request]:
    """Program manager: approve Pending request and atomically reserve full funding. Already Approved is a no-op; other states fail."""
    return respond(lambda: funding_service.approve_request(request_id))


@mcp.tool
def reject_request(request_id: Identifier) -> Result[Request]:
    """Program manager: reject Pending request without changing funding. Already Rejected is a no-op; other states fail."""
    return respond(lambda: funding_service.reject_request(request_id))


@mcp.tool
def record_payment(request_id: Identifier) -> Result[Request]:
    """Finance: simulate full payment of Approved request once. Moves committed to paid; available is unchanged. Already Paid returns the unchanged request and payment metadata."""
    return respond(lambda: funding_service.record_payment(request_id))


@mcp.tool
def get_request(request_id: Identifier) -> Result[Request]:
    """Retrieve a request and its payment fields in the authenticated organization. Foreign and unknown IDs both return NOT_FOUND."""
    return respond(lambda: funding_service.get_request(request_id))


@mcp.tool
def list_requests(status: Status | None = None) -> Result[Requests]:
    """List this organization's requests, optionally filtered by Pending, Approved, Rejected or Paid."""
    return respond(lambda: funding_service.list_requests(status))


@mcp.tool
def funding_summary(fund_id: Identifier | None = None) -> Result[Summaries]:
    """Summarize one or all local funds in USD cents: available = allocation - committed - paid."""
    return respond(lambda: funding_service.funding_summary(fund_id))


def main():
    global funding_service

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    database_path = Path(os.environ.get("ASSISTANCE_DB", "data/assistance.sqlite3"))
    try:
        validate_schema_version(database_path)
        staff_member = resolve_staff_identity(database_path, os.environ.get("ASSISTANCE_IDENTITY"))
        funding_service = FundingService(database_path, staff_member)
    except (ValueError, sqlite3.Error):
        logger.error("Startup rejected: set a valid ASSISTANCE_IDENTITY and initialize ASSISTANCE_DB explicitly")
        raise SystemExit(1)
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
