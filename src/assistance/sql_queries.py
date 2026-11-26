"""Named SQL statements used by the application at runtime."""

CHECK_SCHEMA_VERSION = "PRAGMA user_version"

FIND_STAFF_IDENTITY = """
SELECT staff_id, organization_id, role
FROM staff
WHERE staff_id = ?
"""

FIND_BENEFICIARY_FOR_ORGANIZATION = """
SELECT *
FROM beneficiaries
WHERE organization_id = ? AND beneficiary_id = ?
"""

FIND_FUND_FOR_ORGANIZATION = """
SELECT *
FROM funds
WHERE organization_id = ? AND fund_id = ?
"""

FIND_REQUEST_FOR_ORGANIZATION = """
SELECT requests.*
FROM requests
JOIN funds ON funds.fund_id = requests.fund_id
WHERE funds.organization_id = ? AND requests.request_id = ?
"""

LIST_BENEFICIARIES = """
SELECT beneficiary_id, name
FROM beneficiaries
WHERE organization_id = ?
ORDER BY beneficiary_id
"""

LIST_FUNDS = """
SELECT fund_id, name, allocation_cents
FROM funds
WHERE organization_id = ?
ORDER BY fund_id
"""

LIST_ALL_FUNDS_FOR_ORGANIZATION = """
SELECT *
FROM funds
WHERE organization_id = ?
ORDER BY fund_id
"""

LIST_REQUESTS_FOR_ORGANIZATION = """
SELECT requests.*
FROM requests
JOIN funds ON funds.fund_id = requests.fund_id
WHERE funds.organization_id = ?
  AND (? IS NULL OR requests.status = ?)
ORDER BY requests.created_at, requests.request_id
"""

FIND_REQUEST_BY_CREATION_KEY = """
SELECT *
FROM requests
WHERE created_by_staff_id = ? AND idempotency_key = ?
"""

FUND_TOTALS = """
SELECT
    COALESCE(SUM(CASE WHEN status = 'Approved' THEN amount_cents ELSE 0 END), 0) AS committed_cents,
    COALESCE(SUM(CASE WHEN status = 'Paid' THEN amount_cents ELSE 0 END), 0) AS paid_cents
FROM requests
WHERE fund_id = ?
"""

CREATE_REQUEST = """
INSERT INTO requests(
    request_id,
    beneficiary_id,
    fund_id,
    purpose,
    amount_cents,
    status,
    created_by_staff_id,
    idempotency_key,
    created_at
) VALUES (?, ?, ?, ?, ?, 'Pending', ?, ?, ?)
"""

APPROVE_OR_REJECT_REQUEST = """
UPDATE requests
SET status = ?
WHERE request_id = ?
"""

MARK_REQUEST_PAID = """
UPDATE requests
SET status = 'Paid', paid_by_staff_id = ?, paid_at = ?
WHERE request_id = ?
"""
