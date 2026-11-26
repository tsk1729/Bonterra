"""Reproducible real-stdio demonstration; no language model or network calls."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]


def client_for(database: Path, identity: str) -> Client:
    return Client(StdioTransport(command=sys.executable, args=["-m", "assistance.mcp_server"], cwd=str(ROOT),
                                 env={"ASSISTANCE_DB": str(database.resolve()), "ASSISTANCE_IDENTITY": identity}, keep_alive=False))


async def call(client, name, arguments=None):
    result = await client.call_tool(name, arguments or {})
    data = result.structured_content
    if not data or not data["ok"]:
        raise RuntimeError(f"{name} failed: {data}")
    return data["data"]


async def demonstrate(database: Path):
    async with client_for(database, "udaan_case_worker") as worker:
        tools = await worker.list_tools()
        print(json.dumps({"discovered_tools": [tool.name for tool in tools]}, indent=2))
        assert len(tools) == 9 and all(tool.input_schema is not None and tool.output_schema is not None for tool in tools)
        print(json.dumps({"beneficiaries": await call(worker, "list_beneficiaries"), "funds": await call(worker, "list_funds")}, indent=2))
        initial = await call(worker, "funding_summary", {"fund_id": "udaan_emergency"})
        request = await call(worker, "create_request", dict(beneficiary_id="udaan_ananya", fund_id="udaan_emergency", purpose="Emergency rent", amount_cents=30_000, idempotency_key=f"demo-{uuid4().hex}"))
        print(json.dumps({"created": request}, indent=2))
    async with client_for(database, "udaan_program_manager") as manager:
        approved = await call(manager, "approve_request", {"request_id": request["request_id"]})
        print(json.dumps({"approved": approved, "summary": await call(manager, "funding_summary", {"fund_id":"udaan_emergency"})}, indent=2))
    async with client_for(database, "udaan_finance") as finance:
        paid = await call(finance, "record_payment", {"request_id": request["request_id"]})
        final = await call(finance, "funding_summary", {"fund_id":"udaan_emergency"})
        assert final["funds"][0]["available_cents"] == initial["funds"][0]["available_cents"] - 30_000
        assert paid["status"] == "Paid" and paid["amount_cents"] == 30_000
        assert paid["currency"] == final["funds"][0]["currency"] == "USD"
        assert paid["paid_by_staff_id"] == "udaan_finance" and paid["paid_at"] is not None
        print(json.dumps({"paid": paid, "retrieved": await call(finance, "get_request", {"request_id": request["request_id"]}), "final_summary": final}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/assistance.sqlite3")
    asyncio.run(demonstrate(parser.parse_args().db))
