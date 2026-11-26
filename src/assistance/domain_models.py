"""Strict public contracts; monetary values are integer USD cents (R02/R09)."""

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,100}$")]
Cents = Annotated[int, Field(strict=True, gt=0, le=1_000_000_000)]
Purpose = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]
Status = Literal["Pending", "Approved", "Rejected", "Paid"]
Role = Literal["case_worker", "program_manager", "finance"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateRequest(Model):
    beneficiary_id: Identifier
    fund_id: Identifier
    purpose: Purpose
    amount_cents: Cents
    idempotency_key: Identifier


class StaffIdentity(Model):
    staff_id: Identifier
    organization_id: Identifier
    role: Role


class Beneficiary(Model):
    beneficiary_id: str
    name: str


class Fund(Model):
    fund_id: str
    name: str
    allocation_cents: int
    currency: Literal["USD"] = "USD"


class Request(Model):
    request_id: str
    beneficiary_id: str
    fund_id: str
    purpose: str
    amount_cents: int
    currency: Literal["USD"] = "USD"
    status: Status
    created_by_staff_id: str
    created_at: str
    paid_by_staff_id: str | None = None
    paid_at: str | None = None


class FundSummary(Fund):
    committed_cents: int
    paid_cents: int
    available_cents: int


class Beneficiaries(Model):
    beneficiaries: list[Beneficiary]


class Funds(Model):
    funds: list[Fund]


class Requests(Model):
    requests: list[Request]


class Summaries(Model):
    funds: list[FundSummary]


class Error(Model):
    code: Literal["FORBIDDEN", "NOT_FOUND", "INVALID_STATE", "INSUFFICIENT_FUNDS", "RETRYABLE_BUSY", "INTERNAL_ERROR"]
    message: str
    retryable: bool = False


T = TypeVar("T")


class Result(Model, Generic[T]):
    ok: bool
    data: T | None = None
    error: Error | None = None

    @model_validator(mode="after")
    def validate_result(self):
        if self.ok:
            if self.data is None:
                raise ValueError("Successful response must contain data")
            if self.error is not None:
                raise ValueError("Successful response cannot contain an error")
        else:
            if self.error is None:
                raise ValueError("Failed response must contain an error")
            if self.data is not None:
                raise ValueError("Failed response cannot contain data")
        return self
