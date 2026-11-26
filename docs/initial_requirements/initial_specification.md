# Emergency Assistance Funding — initial specification

Saved before implementation, 2026-11-24 18:05 UTC. This file is the immutable baseline; see REVISIONS.md for significant changes. Time budget: three hours active work, starting 18:02 UTC.

## Requirements and behavior

| ID | Requirement |
|---|---|
| R01 | Each server process resolves a required trusted fixture identity to one nonprofit and role; missing/unknown identity fails startup. All roles may read only their nonprofit's participants, funds, requests and summaries. |
| R02 | Only case workers create immutable requests for existing same-nonprofit participants and funds. Purpose is trimmed nonempty text, maximum 500 characters. USD amounts are positive integer cents, never floats, maximum 1,000,000,000 cents per request. New requests are Pending and reserve nothing. |
| R03 | Only program managers approve Pending requests, atomically reserving the entire amount if available funding suffices. Insufficient funds leave the request Pending. |
| R04 | Only program managers reject Pending requests; no budget changes. No other state transitions except R05. |
| R05 | Only finance staff pay Approved requests, for their full amount, once. Payment changes Approved to Paid and records a unique payment with actor and timestamp. |
| R06 | Per fund: committed = sum of Approved amounts; paid = sum of Paid amounts; available = allocation − committed − paid. Pending/Rejected contribute zero. All money uses integer cents in SQLite and JSON. |
| R07 | Repeating the same approve/reject/pay on its resulting state succeeds without mutation. Approve on Paid, approve on Rejected, reject on Approved/Paid, and pay on Pending/Rejected return INVALID_STATE. Creation requires a caller-scoped idempotency key: identical normalized payload returns original request, even after state changes; changed payload returns IDEMPOTENCY_CONFLICT. Check authorization before replay. |
| R08 | Invalid, forbidden, unavailable or unsuccessful actions leave business state unchanged. Scoped lookup returns NOT_FOUND for both nonexistent and foreign identifiers. All mutations transact; simultaneous competing approvals cannot overspend. |
| R09 | A FastMCP stdio server exposes discoverable tools with validated inputs, explicit structured output schemas, documented errors and no caller-controlled role/tenant. Logs go to stderr. |
| R10 | Persistent SQLite, deterministic synthetic fixtures for two nonprofits, six staff identities, explicit setup/reset separate from startup, pinned dependencies and reproducible commands. Startup never seeds/resets. |
| R11 | Automated service and MCP end-to-end tests, including concurrency and authorization; demonstration discovers schemas and completes create → approve → pay → summary with separate role-bound clients. |

Users: case workers submit support, managers allocate grants, finance records simulated disbursements. Goal: reliable balances and secure access through MCP. Non-goals: UI, real payments, remote hosting, OAuth implementation, resources/prompts, participant intake, partial payments, cancellations, refunds and external payment failures.

## Data model and transaction boundary

Nonprofit owns staff, participant and fund rows. Request references participant/fund through composite foreign keys including nonprofit_id, plus creator staff. Request holds id, nonprofit_id, participant_id, fund_id, purpose, amount_cents, status, creator, idempotency_key and creation/update timestamps. Unique (nonprofit_id, creator_id, idempotency_key) prevents duplicate submissions. Payment has unique request_id, nonprofit_id, amount_cents, payer and timestamp. Transition history records actor, prior/new state and timestamp, in the same transaction. Allocation and request amounts have SQL checks. No edit/delete tools exist.

SQLite BEGIN IMMEDIATE obtains a writer reservation **before** budget reads and status checks. Approval computes sums and updates state in that transaction; competing connections serialize and re-read committed data. Payment insert/state/history commit together. Roll back on every exception; bounded SQLite wait returns RETRYABLE_BUSY, safe to retry using the same key/request. Each read tool uses one read transaction for a consistent snapshot. Derive balances from requests rather than mutable counters to avoid drift. All SQL is parameterized and tenant scoped. Database file and launcher configuration are trusted local infrastructure, not user input.

## MCP contract

IDs/keys: strict nonempty strings of up to 100 ASCII letters, digits, `_` or `-`. No role, nonprofit or actor arguments. Tool inputs reject extra fields and type coercion. Outputs use Pydantic models and FastMCP-generated JSON schemas, exported to docs/tool-schemas.json. All results use `{ok, data, error}`: success has typed data and null error; domain failure has null data and `{code,message,retryable}`. FastMCP validation failures use MCP isError responses; protocol errors follow MCP. Unexpected failures are masked and logged without request text. Clients must inspect ok as well as protocol isError.

| Tool | Inputs | Success data / side effect |
|---|---|---|
| list_participants | none | participants[]: id, name |
| list_funds | none | funds[]: id, name, allocation_cents, currency |
| create_request | participant_id, fund_id, purpose, amount_cents, idempotency_key | request; creates Pending once |
| approve_request | request_id | request; reserves on Pending → Approved |
| reject_request | request_id | request; Pending → Rejected |
| record_payment | request_id | request + payment; Approved → Paid |
| get_request | request_id | request, including payment if present |
| list_requests | optional status enum | requests[] ordered by creation time/id |
| funding_summary | optional fund_id | funds[]: id, allocation_cents, committed_cents, paid_cents, available_cents, currency |

Request output exposes its ids, purpose, amount_cents, USD currency, status, creator and timestamps; payment exposes id, amount_cents, payer and timestamp. Domain codes: FORBIDDEN, NOT_FOUND, INVALID_STATE, INSUFFICIENT_FUNDS, IDEMPOTENCY_CONFLICT, RETRYABLE_BUSY. Infrastructure failures expose INTERNAL_ERROR without SQL, filesystem or traceback detail.

## Acceptance criteria

1. R02–R06: $10,000 fund → submit $300: available 1,000,000 cents; approve: committed 30,000, available 970,000; pay: committed 0, paid 30,000, available 970,000.
2. R03/R08: with 50,000 cents left, two independent simultaneous approvals of 40,000 cents yield exactly one Approved and one INSUFFICIENT_FUNDS; 10,000 remains.
3. R07: duplicate create with same key/payload gives same id, changed purpose fails; repeated approve/pay cause one reservation/payment/history transition. Invalid transitions preserve complete business snapshots.
4. R01/R08: wrong role fails before changing data; all reads and writes reject foreign ids without exposing existence. Forged actor arguments fail validation. Instruction-like purpose text remains inert data.
5. R02/R09: reject zero, negative, fractional, boolean, string and oversized amounts, blank purpose, missing/extra arguments. Missing/unknown identity fails startup. R10/R11: restarts preserve requests; ordinary startup does not seed; stdio client discovers input/output schemas and completes lifecycle.

## Ordered implementation and verification gates

1. Save this baseline. Gate: requirements, retry semantics and isolation decisions reviewable before code.
2. Add package/lockfile, schema/reset, models and transaction service (depends on 1). Gate: service tests for lifecycle, retry, permissions, rollback and competing connections.
3. Add FastMCP wrappers and trusted launcher (depends on 2). Gate: real stdio tests verify discovery, validation, role/tenant boundaries and full lifecycle.
4. Add demonstration, schema export and concise operational/security/AI notes (depends on 3). Gate: fresh locked install, reset, full tests and demo; retain actual outputs, traceability and elapsed time. Stop at three hours with limitations.

AI work is scoped to these ordered tasks in this chat; no independent subagents are used. For any delegated AI change, provide requirement IDs, allowed files and exact acceptance checks; inspect diffs, especially SQL transactions and identity flow, and run independent tests before accepting. AI-generated code alone is not evidence of correctness.

## Production security and open questions

For future HTTP hosting, integrate an existing OAuth provider using MCP authorization discovery and an official validation library. Validate token signature against trusted issuer JWKS with rotation, fixed allowed algorithms, issuer, server audience/resource, expiry and not-before; never accept arbitrary issuers or pass tokens downstream. Map stable (issuer, subject) to active server-side nonprofit memberships and roles, enforce scopes plus business roles on every call, and bind any tenant selection to verified membership. Use TLS, origin validation, rate limits, secrets management and audited revocation policy. Fixture environment identity is local-only and is not HTTP authentication.

Clarify currency/limits, fund restrictions/expiry, multiple memberships, retention and approval separation with business. Proceed assuming USD only, no eligibility/expiry rules, one role/nonprofit per fixture identity, managers may approve any local request, all local records readable, no real participant PII, immutable fund allocations, unbounded retention for this exercise and empty initial requests. Lists are unpaginated for small fixtures; pagination is a production follow-up.
