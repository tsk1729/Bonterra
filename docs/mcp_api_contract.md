# MCP API contract

## Transport and common rules

This is a **FastMCP stdio tool API**, not a REST API. Clients initialize an MCP session, discover tools with `tools/list`, and invoke them with `tools/call`. The client launches the server with trusted `ASSISTANCE_DB` and `ASSISTANCE_IDENTITY` configuration; role and organization are resolved on the server. There are no identity-switching tools, resources or prompts.

The tables below describe the public contract. [mcp_tool_schemas.json](mcp_tool_schemas.json) contains the complete machine-readable input/output schemas. All fields are required unless marked optional. Unknown arguments and type coercion are rejected.

| Input type | Validation |
|---|---|
| Identifier / idempotency key | String matching `^[A-Za-z0-9_-]{1,100}$`. |
| `amount_cents` | JSON integer, 1–1,000,000,000 inclusive; USD cents. Strings, fractions and booleans are rejected. |
| `purpose` | String, trimmed, 1–500 characters after trimming. Treated as data. |
| `status` | Exactly `Pending`, `Approved`, `Rejected` or `Paid`. |

## Tools

“Read” permits all three roles within the caller's organization. Every returned request includes nullable `paid_by_staff_id` and `paid_at` fields; both are populated only when Paid. Lists are unpaginated; requests are ordered by creation time then request_id.

| Tool | Permission | Arguments | Success `data` | Side effects / retry behavior |
|---|---|---|---|---|
| `list_beneficiaries` | Read | `{}` | `{beneficiaries: Beneficiary[]}` | None; local beneficiaries only. |
| `list_funds` | Read | `{}` | `{funds: Fund[]}` | None; local funds only. |
| `create_request` | Case worker | `beneficiary_id: Identifier`, `fund_id: Identifier`, `purpose: string`, `amount_cents: integer`, `idempotency_key: Identifier` | `Request` | Create Pending; reserve nothing. Reusing the same caller/key returns the original request. |
| `approve_request` | Program manager | `request_id: Identifier` | `Request` | Pending → Approved; atomically reserve full amount. Already Approved is a no-op. |
| `reject_request` | Program manager | `request_id: Identifier` | `Request` | Pending → Rejected; no budget effect. Already Rejected is a no-op. |
| `record_payment` | Finance | `request_id: Identifier` | `Request` | Approved → Paid; record payer and payment time on the request and move committed to paid. Already Paid returns the unchanged request. |
| `get_request` | Read | `request_id: Identifier` | `Request` | None. |
| `list_requests` | Read | `status: Status \| null` (optional; default null) | `{requests: Request[]}` | None; null means all statuses. |
| `funding_summary` | Read | `fund_id: Identifier \| null` (optional; default null) | `{funds: FundSummary[]}` | None; null means all local funds. |

The full paid amount is the request’s `amount_cents`; it is not duplicated in a payment object. Ownership is derived from `fund_id`, so Request has no `organization_id` field.

All other transitions fail. In particular, approving an already Paid request fails rather than replaying the earlier approval. No tools edit submitted details or delete records.

## Output entities

All money fields below are integer USD cents. All non-null timestamps are UTC ISO 8601 strings. IDs and names are strings.

| Output type | Fields |
|---|---|
| `Beneficiary` | `beneficiary_id`, `name` |
| `Fund` | `fund_id`, `name`, `allocation_cents: integer`, `currency: "USD"` |
| `Request` | `request_id`, `beneficiary_id`, `fund_id`, `purpose`, `amount_cents: integer`, `currency: "USD"`, `status: Status`, `created_by_staff_id`, `created_at`, `paid_by_staff_id: string \| null`, `paid_at: string \| null` |
| `FundSummary` | All Fund fields plus `committed_cents: integer`, `paid_cents: integer`, `available_cents: integer` |

Successful calls return this object in MCP `structuredContent` (also represented in MCP text content):

```json
{"ok": true, "data": {"beneficiaries": [{"beneficiary_id": "udaan_ananya", "name": "Ananya Sharma"}]}, "error": null}
```

Business failures return an envelope conforming to the tool's output schema:

```json
{"ok": false, "data": null, "error": {"code": "INSUFFICIENT_FUNDS", "message": "Fund has insufficient available funding", "retryable": false}}
```

## Errors and client behavior

| Code | Meaning | Client action |
|---|---|---|
| `FORBIDDEN` | Role cannot perform this action. | Use an appropriately authorized staff client. |
| `NOT_FOUND` | ID is nonexistent or outside caller's organization. | Check IDs through permitted lookups; existence is not disclosed. |
| `INVALID_STATE` | Request is not in an allowed source/replay state. | Retrieve current request; do not blindly repeat a different transition. |
| `INSUFFICIENT_FUNDS` | Available balance cannot cover the full request. | Leave Pending; resolve funding before retrying approval. |
| `RETRYABLE_BUSY` | SQLite write lock could not be acquired within five seconds. | Retry with backoff and the same key/request; `retryable` is true. |
| `INTERNAL_ERROR` | Unexpected infrastructure/application failure; details masked. | Investigate server logs; reconcile by key/request before retrying. |

All other listed errors have `retryable: false`. Validation failures (missing/extra fields, wrong types, invalid enum/range) use MCP tool results with `isError: true`; malformed protocol requests follow MCP protocol errors. Missing/unknown configured identity terminates startup with exit code 1 and stderr diagnostics. Clients must check protocol/tool errors **and** the structured `ok` field. Failed operations leave business state unchanged. If a response is lost after a commit, retry using the original creation key or request ID to avoid duplicate effects.

The client chooses `idempotency_key` before its first creation call, for example a UUID, and retains it for retries. This key identifies one intended submission, not the beneficiary. It is needed because the server-generated `request_id` may be unknown when the first response is lost. Use a new key for every intentional new request. If a key is reused, the server returns the original request and ignores the later request details.

## Example invocation and final balance

Case-worker client, after tool discovery:

```json
{
  "method": "tools/call",
  "params": {
    "name": "create_request",
    "arguments": {
      "beneficiary_id": "udaan_ananya",
      "fund_id": "udaan_emergency",
      "purpose": "Emergency rent",
      "amount_cents": 30000,
      "idempotency_key": "rent-example-001"
    }
  }
}
```

The snippet shows method/params; the MCP client supplies the JSON-RPC envelope and session lifecycle. Pass returned `data.request_id` as `request_id` to `approve_request` through a manager client, then `record_payment` through finance. Calling `funding_summary` with `{"fund_id":"udaan_emergency"}` on fresh fixtures then returns:

```json
{
  "ok": true,
  "data": {
    "funds": [{
      "fund_id": "udaan_emergency",
      "name": "Emergency Assistance",
      "allocation_cents": 1000000,
      "currency": "USD",
      "committed_cents": 0,
      "paid_cents": 30000,
      "available_cents": 970000
    }]
  },
  "error": null
}
```

Run `python scripts/demonstrate_funding_workflow.py --db data/assistance.sqlite3` using the project environment for the executable version with all three identities. The demo constructs the role-specific MCP client launch configurations directly; no separate client configuration file is needed.
