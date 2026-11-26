"""Business rules and organization-scoped transactions, independent of MCP."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from assistance.domain_models import Beneficiary, Beneficiaries, CreateRequest, Fund, Funds, FundSummary, Request, Requests, StaffIdentity, Summaries
from assistance.database import transaction
from assistance.sql_queries import (
    APPROVE_OR_REJECT_REQUEST,
    CREATE_REQUEST,
    FIND_BENEFICIARY_FOR_ORGANIZATION,
    FIND_FUND_FOR_ORGANIZATION,
    FIND_REQUEST_BY_CREATION_KEY,
    FIND_REQUEST_FOR_ORGANIZATION,
    FUND_TOTALS,
    LIST_ALL_FUNDS_FOR_ORGANIZATION,
    LIST_BENEFICIARIES,
    LIST_FUNDS,
    LIST_REQUESTS_FOR_ORGANIZATION,
    MARK_REQUEST_PAID,
)


class BusinessError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FundingService:
    def __init__(self, database_path: Path, staff_member: StaffIdentity):
        self.database_path = database_path
        self.staff_member = staff_member

    def require_role(self, role: str):
        if self.staff_member.role != role:
            raise BusinessError("FORBIDDEN", "Role does not permit this action")

    def find_beneficiary(self, db, beneficiary_id: str):
        row = db.execute(FIND_BENEFICIARY_FOR_ORGANIZATION, (self.staff_member.organization_id, beneficiary_id)).fetchone()
        if row is None:
            raise BusinessError("NOT_FOUND", "Record not found")
        return row

    def find_fund(self, db, fund_id: str):
        row = db.execute(FIND_FUND_FOR_ORGANIZATION, (self.staff_member.organization_id, fund_id)).fetchone()
        if row is None:
            raise BusinessError("NOT_FOUND", "Record not found")
        return row

    def find_request(self, db, request_id: str):
        # A request's organization is determined by its fund.
        row = db.execute(FIND_REQUEST_FOR_ORGANIZATION, (self.staff_member.organization_id, request_id)).fetchone()
        if row is None:
            raise BusinessError("NOT_FOUND", "Record not found")
        return row

    def beneficiary_from_row(self, row) -> Beneficiary:
        return Beneficiary(
            beneficiary_id=row["beneficiary_id"],
            name=row["name"],
        )

    def fund_from_row(self, row) -> Fund:
        return Fund(
            fund_id=row["fund_id"],
            name=row["name"],
            allocation_cents=row["allocation_cents"],
        )

    def request_from_row(self, row) -> Request:
        return Request(
            request_id=row["request_id"],
            beneficiary_id=row["beneficiary_id"],
            fund_id=row["fund_id"],
            purpose=row["purpose"],
            amount_cents=row["amount_cents"],
            status=row["status"],
            created_by_staff_id=row["created_by_staff_id"],
            created_at=row["created_at"],
            paid_by_staff_id=row["paid_by_staff_id"],
            paid_at=row["paid_at"],
        )

    def summary_view(self, db, fund) -> FundSummary:
        totals = db.execute(FUND_TOTALS, (fund["fund_id"],)).fetchone()
        return FundSummary(fund_id=fund["fund_id"], name=fund["name"], allocation_cents=fund["allocation_cents"],
                           committed_cents=totals["committed_cents"], paid_cents=totals["paid_cents"],
                           available_cents=fund["allocation_cents"] - totals["committed_cents"] - totals["paid_cents"])

    def list_beneficiaries(self) -> Beneficiaries:
        with transaction(self.database_path) as db:
            rows = db.execute(LIST_BENEFICIARIES, (self.staff_member.organization_id,)).fetchall()
            return Beneficiaries(beneficiaries=[self.beneficiary_from_row(row) for row in rows])

    def list_funds(self) -> Funds:
        with transaction(self.database_path) as db:
            rows = db.execute(LIST_FUNDS, (self.staff_member.organization_id,)).fetchall()
            return Funds(funds=[self.fund_from_row(row) for row in rows])

    def get_request(self, request_id: str) -> Request:
        with transaction(self.database_path) as db:
            return self.request_from_row(self.find_request(db, request_id))

    def list_requests(self, status: str | None = None) -> Requests:
        with transaction(self.database_path) as db:
            rows = db.execute(LIST_REQUESTS_FOR_ORGANIZATION, (self.staff_member.organization_id, status, status)).fetchall()
            return Requests(requests=[self.request_from_row(row) for row in rows])

    def funding_summary(self, fund_id: str | None = None) -> Summaries:
        with transaction(self.database_path) as db:
            if fund_id is not None:
                funds = [self.find_fund(db, fund_id)]
            else:
                funds = db.execute(
                    LIST_ALL_FUNDS_FOR_ORGANIZATION,
                    (self.staff_member.organization_id,),
                ).fetchall()
            return Summaries(funds=[self.summary_view(db, fund) for fund in funds])

    def create_request(self, payload: CreateRequest) -> Request:
        self.require_role("case_worker")
        with transaction(self.database_path, write=True) as db:
            existing = db.execute(FIND_REQUEST_BY_CREATION_KEY, (self.staff_member.staff_id, payload.idempotency_key)).fetchone()
            if existing:
                return self.request_from_row(existing)
            self.find_beneficiary(db, payload.beneficiary_id)
            self.find_fund(db, payload.fund_id)
            request_id, timestamp = uuid4().hex, now()
            db.execute(
                CREATE_REQUEST,
                (request_id, payload.beneficiary_id, payload.fund_id, payload.purpose,
                 payload.amount_cents, self.staff_member.staff_id, payload.idempotency_key, timestamp),
            )
            return self.request_from_row(self.find_request(db, request_id))

    def approve_request(self, request_id: str) -> Request:
        """Approve a Pending request after checking its fund has enough money."""
        self.require_role("program_manager")
        with transaction(self.database_path, write=True) as db:
            row = self.find_request(db, request_id)
            if row["status"] == "Approved":
                return self.request_from_row(row)
            if row["status"] != "Pending":
                raise BusinessError("INVALID_STATE", f"Cannot approve a {row['status']} request")

            fund = self.find_fund(db, row["fund_id"])
            if self.summary_view(db, fund).available_cents < row["amount_cents"]:
                raise BusinessError("INSUFFICIENT_FUNDS", "Fund has insufficient available funding")

            db.execute(APPROVE_OR_REJECT_REQUEST, ("Approved", request_id))
            return self.request_from_row(self.find_request(db, request_id))

    def reject_request(self, request_id: str) -> Request:
        """Reject a Pending request without changing the fund balance."""
        self.require_role("program_manager")
        with transaction(self.database_path, write=True) as db:
            row = self.find_request(db, request_id)
            if row["status"] == "Rejected":
                return self.request_from_row(row)
            if row["status"] != "Pending":
                raise BusinessError("INVALID_STATE", f"Cannot reject a {row['status']} request")

            db.execute(APPROVE_OR_REJECT_REQUEST, ("Rejected", request_id))
            return self.request_from_row(self.find_request(db, request_id))

    def record_payment(self, request_id: str) -> Request:
        """Record the one full simulated payment for an Approved request."""
        self.require_role("finance")
        with transaction(self.database_path, write=True) as db:
            row = self.find_request(db, request_id)
            if row["status"] == "Paid":
                return self.request_from_row(row)
            if row["status"] != "Approved":
                raise BusinessError("INVALID_STATE", f"Cannot pay a {row['status']} request")

            db.execute(MARK_REQUEST_PAID, (self.staff_member.staff_id, now(), request_id))
            return self.request_from_row(self.find_request(db, request_id))


def is_busy(exc: sqlite3.OperationalError) -> bool:
    return getattr(exc, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
