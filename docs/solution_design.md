# Emergency Assistance Funding — design and verification

## 1. Scope and architecture

A headless Python/FastMCP server supports participant lookup, assistance requests, approvals, simulated payments and funding summaries. SQLite provides persistent storage. Synthetic fixtures cover two nonprofits.

`mcp_server.py` defines tools and validates calls; `funding_service.py` enforces business rules; `database.py` manages transactions and trusted identities. See the [MCP contract](mcp_api_contract.md), [tool schemas](mcp_tool_schemas.json) and [run commands](../README.md).

| Role | Read own nonprofit | Create | Approve/reject | Pay |
|---|---|---|---|---|
| Case worker | Yes | Yes | No | No |
| Program manager | Yes | No | Yes | No |
| Finance | Yes | No | No | Yes |

UI, real payments, hosting, OAuth implementation, model integration, MCP resources/prompts and partial payments are outside scope.

## 2. Schema design and business rules

The SQLite [schema](../src/assistance/database_schema.sql) has five tables. `PK` denotes primary key; `FK` denotes foreign key. Identifiers and timestamps are `TEXT`; amounts are `INTEGER`. All non-key fields are required except the two payment fields.

| Table | Primary key | Foreign keys | Other fields |
|---|---|---|---|
| `organizations` | `organization_id` | — | `name` |
| `staff` | `staff_id` | `organization_id → organizations` | `role` |
| `beneficiaries` | `beneficiary_id` | `organization_id → organizations` | `name` |
| `funds` | `fund_id` | `organization_id → organizations` | `name`, `allocation_cents` |
| `requests` | `request_id` | `beneficiary_id → beneficiaries`, `fund_id → funds`, `created_by_staff_id → staff`, nullable `paid_by_staff_id → staff` | `purpose`, `amount_cents`, `status`, `idempotency_key`, `created_at`, nullable `paid_at` |

Relationships are one-to-many: organization → staff/beneficiaries/funds; beneficiary → requests; fund → requests; staff → created/paid requests. Request ownership follows its fund; creation enforces that beneficiary and fund belong to the caller's organization.

Database constraints restrict roles and statuses, require nonnegative fund allocations, positive request amounts up to 1,000,000,000, and purpose length 1–500. `UNIQUE(created_by_staff_id, idempotency_key)` prevents duplicate creation. Both payment fields must be populated exactly when status is Paid. Index `requests(fund_id, status)` supports budget totals. Full payment is stored on the request, once; balances are calculated from request states.

Money is stored and returned as integer USD cents: `amount_cents=30001` means USD 300.01. Floating-point amounts are rejected.

| Action | Transition | Budget effect |
|---|---|---|
| Create | New → Pending | None |
| Approve | Pending → Approved | Reserve full amount if available |
| Reject | Pending → Rejected | None |
| Pay | Approved → Paid | Move committed amount to paid |

`available = allocation − committed − paid`, where committed totals Approved requests and paid totals Paid requests. Requests cannot be edited. Amounts must be positive integers; purpose is trimmed, nonempty and at most 500 characters. Unknown arguments and type coercion are rejected.

Repeating approve/reject/pay in the resulting state returns the saved result. Reusing a creation key returns the original request, even if the supplied payload differs. Other transitions fail without mutation.

Every write uses SQLite `BEGIN IMMEDIATE` before checking state or funds, so competing approvals serialize and recheck the balance. Exceptions roll back; a five-second lock timeout produces a retryable error.

Acceptance examples: approving USD 300.00 against USD 10,000.00 leaves USD 9,700.00 available; payment preserves that balance. Two competing USD 400.00 approvals against USD 500.00 allow one success, leaving USD 100.00.

## 3. Identity and security

`ASSISTANCE_IDENTITY` resolves to a fixture staff member on the server. Missing or unknown identities reject startup. Role checks precede mutations; reads and writes are organization-scoped. Foreign and unknown records both return `NOT_FOUND`. Tool arguments and stored text cannot grant permissions. Logs go to stderr.

For future HTTP hosting, use an existing OAuth provider, validate token signature, issuer, audience and expiry, then map the stable caller identity to active nonprofit membership and role. Enforce membership and business permissions on every call.

## 4. Implementation and verification

Order and gates: initial specification → models/storage/service with service tests → MCP tools with stdio tests → demo/documentation with full-suite verification. Scope AI work by requirement IDs, allowed files and acceptance checks; inspect SQL and identity handling before accepting changes.

Paths below are relative to `src/assistance/`; test names omit `test_`.

| ID | Implementation | Verification |
|---|---|---|
| R01 | `database.resolve_staff_identity`; scoped lookups | roles_and_organization_boundaries; stdio_startup_rejects_bad_identity |
| R02 | `domain_models.CreateRequest`; `FundingService.create_request` | invalid_request_data_is_rejected |
| R03 | `FundingService.approve_request`; `database.transaction` | competing_approvals_do_not_overspend |
| R04 | `FundingService.reject_request` | request_lifecycle_and_persistence |
| R05 | `FundingService.record_payment` | MCP lifecycle test, including payment retry |
| R06 | Integer USD cents; `FundingService.summary_view` | Lifecycle, cent-precision and balance tests |
| R07 | Unique creation key; status-checked retries | reusing_creation_key_returns_original_request; MCP lifecycle test |
| R08 | Role checks, scoped SQL and transactions | Authorization and competing-approval tests; rollback injection untested |
| R09 | `mcp_server.py`; `domain_models.Result` | `tests/test_mcp_integration.py` |
| R10 | SQLite; `seed_database.setup`; explicit reset | Persistence and server-restart tests |
| R11 | MCP integration tests; workflow demo | Real stdio discovery, lifecycle and competing server processes |

Latest checks: **14 tests passed, 1 deprecation warning** on Python 3.14/macOS arm64. Coverage includes one-cent amounts, amounts with dollar and cent components, payment retries and rejection of the old database version. The USD demo completed create → approve → pay → summary against fresh temporary storage, exiting 0. Actual outputs are saved for [tests](../evidence/test_results.txt) and the [demo](../evidence/funding_workflow_demo.txt). A fresh dependency installation was not rerun during this review.

## 5. AI workflow examples

Contexts summarize recorded work rather than quote prompts verbatim.

| Context | AI contribution | Independent evidence |
|---|---|---|
| Check explicit assignment constraints against implementation assumptions. | Identified and corrected the INR-rupees mismatch by using integer USD cents across storage and MCP contracts. | Assignment compared with monetary fields, generated schemas and cent-precision lifecycle tests. |
| Simplify test calls while retaining stdio connections and concurrent approvals. | Added a synchronous adapter around FastMCP's async client; retained competing calls through two threads. | At refactor: **4 integration tests passed**. Current full suite: **14 passed, 1 warning**. |
| Assess whether tests support documented correctness claims. | Reviewed locking, corrected an unsupported test claim, refreshed evidence and added cent-precision checks. | Full suite: **14 passed, 1 warning**; temporary-database USD demo: **exit 0**. |

## 6. Revisions, assumptions and limitations

The [initial specification](initial_requirements/initial_specification.md) is preserved. Subsequent simplifications use five tables, service-level organization checks and payment fields on requests. Creation retries return the original request rather than the originally specified payload-conflict error. Tests now expose synchronous calls while retaining async transport internally. Schema version 8 replaces INR rupee fields with USD cent fields to meet the assignment; older synthetic databases require explicit reset rather than automatic currency conversion.

**Limitations:** some forbidden transitions and injected rollback failures lack explicit tests. Other Python versions/platforms remain unverified.

Assumptions: fixed allocations, one nonprofit/role per staff member, no fund expiry or eligibility rules, and unpaginated lists. Clarify these before production.

**Total active time: 2 hours 10 minutes**, as reported by the author. Priorities: resolve remaining specification differences and verification gaps. Production identity, migrations and pagination are deferred.
