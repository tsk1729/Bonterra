"""Essential service-level tests for the emergency funding workflow."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from assistance.funding_service import BusinessError
from conftest import funding_service, payload


def assert_error(expected_code, operation):
    with pytest.raises(BusinessError) as caught:
        operation()
    assert caught.value.code == expected_code


def test_request_lifecycle_and_persistence(database):
    worker = funding_service(database, "case_worker")
    manager = funding_service(database, "program_manager")
    finance = funding_service(database, "finance")

    request = worker.create_request(payload())
    assert request.status == "Pending"

    approved = manager.approve_request(request.request_id)
    assert approved.status == "Approved"

    paid = finance.record_payment(request.request_id)
    assert paid.status == "Paid"
    assert paid.paid_by_staff_id == "udaan_finance"
    assert paid.paid_at is not None

    restarted_worker = funding_service(database, "case_worker")
    saved_request = restarted_worker.get_request(request.request_id)
    assert saved_request == paid

    summary = restarted_worker.funding_summary("udaan_emergency").funds[0]
    assert summary.committed_cents == 0
    assert summary.paid_cents == 30_000
    assert summary.available_cents == 970_000

    rejected_request = worker.create_request(
        payload(idempotency_key="rejected-request")
    )
    rejected = manager.reject_request(rejected_request.request_id)
    assert rejected.status == "Rejected"


def test_reusing_creation_key_returns_original_request(database):
    worker = funding_service(database, "case_worker")

    original = worker.create_request(payload(purpose="Emergency rent"))
    retried = worker.create_request(payload(amount_cents=1))

    assert retried == original
    assert worker.list_requests().requests == [original]


@pytest.mark.parametrize("amount_cents", [1, 101, 30_001])
def test_usd_cent_precision_through_payment(database, amount_cents):
    worker = funding_service(database, "case_worker")
    manager = funding_service(database, "program_manager")
    finance = funding_service(database, "finance")
    request = worker.create_request(payload(amount_cents=amount_cents))
    initial = worker.funding_summary("udaan_emergency").funds[0]
    assert request.currency == initial.currency == "USD"
    assert request.amount_cents == amount_cents
    assert initial.available_cents == initial.allocation_cents

    manager.approve_request(request.request_id)
    approved = worker.funding_summary("udaan_emergency").funds[0]
    assert approved.committed_cents == amount_cents
    assert approved.available_cents == initial.allocation_cents - amount_cents

    paid = finance.record_payment(request.request_id)
    assert finance.record_payment(request.request_id) == paid
    final = worker.funding_summary("udaan_emergency").funds[0]
    assert final.committed_cents == 0
    assert final.paid_cents == amount_cents
    assert final.available_cents == approved.available_cents


def test_roles_and_organization_boundaries(database):
    udaan_worker = funding_service(database, "case_worker")
    udaan_manager = funding_service(database, "program_manager")
    udaan_finance = funding_service(database, "finance")
    asha_worker = funding_service(database, "case_worker", "asha")
    asha_manager = funding_service(database, "program_manager", "asha")

    udaan_request = udaan_worker.create_request(payload())
    asha_request = asha_worker.create_request(
        payload(beneficiary_id="asha_kavya", fund_id="asha_emergency")
    )

    assert_error(
        "FORBIDDEN",
        lambda: udaan_worker.approve_request(udaan_request.request_id),
    )
    assert_error(
        "FORBIDDEN",
        lambda: udaan_manager.create_request(payload()),
    )
    assert_error(
        "INVALID_STATE",
        lambda: udaan_finance.record_payment(udaan_request.request_id),
    )
    assert_error("NOT_FOUND", lambda: udaan_manager.get_request(asha_request.request_id))
    assert_error("NOT_FOUND", lambda: asha_manager.approve_request(udaan_request.request_id))


def test_invalid_request_data_is_rejected(database):
    worker = funding_service(database, "case_worker")

    with pytest.raises(ValidationError):
        payload(amount_cents=0)

    with pytest.raises(ValidationError):
        payload(amount_cents=1.5)

    with pytest.raises(ValidationError):
        payload(purpose=" ")

    assert worker.list_requests().requests == []


def test_competing_approvals_do_not_overspend(database):
    worker = funding_service(database, "case_worker")
    first_request = worker.create_request(
        payload(
            fund_id="udaan_transport",
            amount_cents=40_000,
            idempotency_key="transport-one",
        )
    )
    second_request = worker.create_request(
        payload(
            fund_id="udaan_transport",
            amount_cents=40_000,
            idempotency_key="transport-two",
        )
    )
    barrier = Barrier(2)

    def approve(request):
        manager = funding_service(database, "program_manager")
        barrier.wait(timeout=5)
        try:
            return manager.approve_request(request.request_id).status
        except BusinessError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_approval = pool.submit(approve, first_request)
        second_approval = pool.submit(approve, second_request)

        results = [
            first_approval.result(),
            second_approval.result(),
        ]

    assert sorted(results) == ["Approved", "INSUFFICIENT_FUNDS"]
    summary = worker.funding_summary("udaan_transport").funds[0]
    assert summary.committed_cents == 40_000
    assert summary.available_cents == 10_000
