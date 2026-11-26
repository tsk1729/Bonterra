"""Black-box tests cross real MCP stdio and separate role-bound server processes."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import pytest
from anyio.from_thread import start_blocking_portal
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]


class SyncMCPClient:
    """Let tests make blocking calls while FastMCP runs on a background event loop."""

    def __init__(self, client, portal):
        self.client = client
        self.portal = portal

    def list_tools(self):
        return self.portal.call(self.client.list_tools)

    def list_resources(self):
        return self.portal.call(self.client.list_resources)

    def list_prompts(self):
        return self.portal.call(self.client.list_prompts)

    def call_tool(self, name, arguments=None, *, raise_on_error=False):
        return self.portal.call(
            partial(
                self.client.call_tool,
                name,
                arguments or {},
                raise_on_error=raise_on_error,
            )
        )


@contextmanager
def client_for(database, identity):
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "assistance.mcp_server"],
        cwd=str(ROOT),
        env={
            "ASSISTANCE_DB": str(database),
            "ASSISTANCE_IDENTITY": identity,
        },
        keep_alive=False,
    )
    # Keep one event loop alive for the entire connection. The wrapper opens
    # and closes the async context in the same task, as FastMCP requires.
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(transport)) as client:
            yield SyncMCPClient(client, portal)


def result(client, name, arguments=None):
    response = client.call_tool(name, arguments or {}, raise_on_error=False)
    assert not response.is_error, response
    assert response.structured_content is not None
    return response.structured_content


def ok(client, name, arguments=None):
    response = result(client, name, arguments)
    assert response["ok"], response
    return response["data"]


def creation(**changes):
    data = {
        "beneficiary_id": "udaan_ananya",
        "fund_id": "udaan_emergency",
        "purpose": "Rent",
        "amount_cents": 30_000,
        "idempotency_key": "mcp-rent",
    }
    data.update(changes)
    return data


def test_mcp_lifecycle_discovery_validation_and_security(database):
    with (
        client_for(database, "udaan_case_worker") as worker,
        client_for(database, "udaan_program_manager") as manager,
        client_for(database, "udaan_finance") as finance,
        client_for(database, "asha_program_manager") as asha_manager,
    ):
        tools = worker.list_tools()
        tool_names = {tool.name for tool in tools}
        assert tool_names == {
            "list_beneficiaries",
            "list_funds",
            "create_request",
            "approve_request",
            "reject_request",
            "record_payment",
            "get_request",
            "list_requests",
            "funding_summary",
        }
        assert all(t.input_schema and t.output_schema for t in tools)
        assert not worker.list_resources()
        assert not worker.list_prompts()

        for actor in (worker, manager, finance):
            assert len((ok(actor, "list_beneficiaries"))["beneficiaries"]) == 2
            assert len((ok(actor, "list_funds"))["funds"]) == 2
            assert (ok(actor, "list_requests"))["requests"] == []

        invalid_requests = [
            creation(amount_cents=0),
            creation(amount_cents=-1),
            creation(amount_cents=1.5),
            creation(amount_cents=True),
            creation(amount_cents="300"),
            creation(amount_cents=1_000_000_001),
            creation(purpose=" "),
            creation(purpose="x" * 501),
            creation(beneficiary_id="x' OR 1=1"),
            creation(role="program_manager"),
            creation(organization_id="asha"),
            {},
        ]
        for invalid_request in invalid_requests:
            response = worker.call_tool(
                "create_request",
                invalid_request,
                raise_on_error=False,
            )
            assert response.is_error, invalid_request
        assert (ok(worker, "list_requests"))["requests"] == []
        assert (result(worker, "create_request", creation(beneficiary_id="asha_kavya")))["error"]["code"] == "NOT_FOUND"
        assert (result(manager, "create_request", creation()))["error"]["code"] == "FORBIDDEN"

        request = ok(worker, "create_request", creation())
        request_id = {"request_id": request["request_id"]}
        assert request["status"] == "Pending"
        assert (ok(worker, "create_request", creation()))["request_id"] == request["request_id"]
        assert (ok(worker, "create_request", creation(amount_cents=1))) == request
        for client, tool in ((worker,"approve_request"),(worker,"reject_request"),(worker,"record_payment"),(finance,"approve_request"),(manager,"record_payment")):
            assert (result(client, tool, request_id))["error"]["code"] == "FORBIDDEN"
        assert (result(finance, "record_payment", request_id))["error"]["code"] == "INVALID_STATE"
        for tool in ("get_request", "approve_request", "reject_request"):
            response = result(asha_manager, tool, request_id)
            assert response["error"]["code"] == "NOT_FOUND"

        response = result(
            asha_manager,
            "funding_summary",
            {"fund_id": "udaan_emergency"},
        )
        assert response["error"]["code"] == "NOT_FOUND"
        assert (ok(asha_manager, "list_requests"))["requests"] == []

        asha_beneficiaries = ok(asha_manager, "list_beneficiaries")
        for beneficiary in asha_beneficiaries["beneficiaries"]:
            assert beneficiary["beneficiary_id"].startswith("asha_")

        pending = (ok(worker, "funding_summary", {"fund_id":"udaan_emergency"}))["funds"][0]
        assert pending["available_cents"] == 1_000_000
        approved = ok(manager, "approve_request", request_id)
        assert approved["status"] == "Approved"
        assert ok(manager, "approve_request", request_id) == approved
        committed = (ok(worker, "funding_summary", {"fund_id":"udaan_emergency"}))["funds"][0]
        assert (committed["committed_cents"], committed["paid_cents"], committed["available_cents"]) == (30_000,0,970_000)
        paid = ok(finance, "record_payment", request_id)
        assert paid["status"] == "Paid" and paid["amount_cents"] == 30_000
        assert paid["paid_by_staff_id"] == "udaan_finance" and paid["paid_at"] is not None
        assert ok(finance, "record_payment", request_id) == paid
        assert ok(finance, "get_request", request_id) == paid
        final = (ok(worker, "funding_summary", {"fund_id":"udaan_emergency"}))["funds"][0]
        assert (final["committed_cents"], final["paid_cents"], final["available_cents"]) == (0,30_000,970_000)
        assert (ok(worker, "list_requests", {"status":"Paid"}))["requests"] == [paid]

        rejectable = ok(worker, "create_request", creation(idempotency_key="reject"))
        rejected = ok(manager, "reject_request", {"request_id":rejectable["request_id"]})
        assert rejected["status"] == "Rejected"
        assert ok(manager, "reject_request", {"request_id":rejectable["request_id"]}) == rejected
        assert (ok(worker, "funding_summary", {"fund_id":"udaan_emergency"}))["funds"][0] == final
    # A new process reads persisted state; startup must not reset the DB.
    with client_for(database, "udaan_finance") as restarted:
        assert ok(restarted, "get_request", request_id) == paid


def test_mcp_competing_approvals_across_processes(database):
    with client_for(database, "udaan_case_worker") as worker:
        first_request = ok(
            worker,
            "create_request",
            creation(
                fund_id="udaan_transport",
                amount_cents=40_000,
                idempotency_key="race-one",
            ),
        )
        second_request = ok(
            worker,
            "create_request",
            creation(
                fund_id="udaan_transport",
                amount_cents=40_000,
                idempotency_key="race-two",
            ),
        )

    with client_for(database, "udaan_program_manager") as first_manager:
        with client_for(database, "udaan_program_manager") as second_manager:
            # Two threads submit blocking calls to separate server processes.
            # Keep the approval attempts concurrent to exercise budget safety.
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_approval = executor.submit(
                    result,
                    first_manager,
                    "approve_request",
                    {"request_id": first_request["request_id"]},
                )
                second_approval = executor.submit(
                    result,
                    second_manager,
                    "approve_request",
                    {"request_id": second_request["request_id"]},
                )
                responses = [first_approval.result(), second_approval.result()]

            successful_responses = [response for response in responses if response["ok"]]
            failed_responses = [response for response in responses if not response["ok"]]
            assert len(successful_responses) == 1
            assert len(failed_responses) == 1
            assert failed_responses[0]["error"]["code"] == "INSUFFICIENT_FUNDS"

            summary = ok(
                first_manager,
                "funding_summary",
                {"fund_id": "udaan_transport"},
            )
            transport_fund = summary["funds"][0]
            assert transport_fund["committed_cents"] == 40_000
            assert transport_fund["available_cents"] == 10_000


@pytest.mark.parametrize("identity", [None, "unknown"])
def test_stdio_startup_rejects_bad_identity(database, identity):
    env = {**os.environ, "ASSISTANCE_DB": str(database)}
    env.pop("ASSISTANCE_IDENTITY", None)
    if identity is not None:
        env["ASSISTANCE_IDENTITY"] = identity

    process = subprocess.run(
        [sys.executable, "-m", "assistance.mcp_server"],
        input="",
        text=True,
        capture_output=True,
        env=env,
        cwd=ROOT,
        timeout=30,
    )
    assert process.returncode == 1
    assert process.stdout == ""
    assert "Startup rejected" in process.stderr


def test_mcp_usd_cent_precision(database):
    with (
        client_for(database, "udaan_case_worker") as worker,
        client_for(database, "udaan_program_manager") as manager,
        client_for(database, "udaan_finance") as finance,
    ):
        tool = next(tool for tool in worker.list_tools() if tool.name == "create_request")
        assert "amount_cents" in tool.input_schema["required"]
        assert "amount_rupees" not in tool.input_schema["properties"]
        for invalid in (creation(amount_cents=0.01), creation(amount_cents=1.0)):
            assert worker.call_tool("create_request", invalid).is_error

        request = ok(worker, "create_request", creation(amount_cents=30_001))
        assert request["amount_cents"] == 30_001
        assert request["currency"] == "USD"
        request_id = {"request_id": request["request_id"]}
        ok(manager, "approve_request", request_id)
        paid = ok(finance, "record_payment", request_id)
        assert ok(finance, "record_payment", request_id) == paid
        summary = ok(finance, "funding_summary", {"fund_id": "udaan_emergency"})["funds"][0]
        assert summary["currency"] == "USD"
        assert (summary["committed_cents"], summary["paid_cents"], summary["available_cents"]) == (0, 30_001, 969_999)


def test_stdio_startup_rejects_old_currency_schema(database):
    import sqlite3

    with sqlite3.connect(database) as db:
        db.execute("PRAGMA user_version = 7")
    process = subprocess.run(
        [sys.executable, "-m", "assistance.mcp_server"],
        input="",
        text=True,
        capture_output=True,
        env={**os.environ, "ASSISTANCE_DB": str(database), "ASSISTANCE_IDENTITY": "udaan_case_worker"},
        cwd=ROOT,
        timeout=30,
    )
    assert process.returncode == 1
    assert process.stdout == ""
    assert "Startup rejected" in process.stderr
