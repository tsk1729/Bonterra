PRAGMA foreign_keys = ON;
CREATE TABLE organizations (
    organization_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE staff (
    staff_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
    role TEXT NOT NULL CHECK(role IN ('case_worker','program_manager','finance'))
);
CREATE TABLE beneficiaries (
    beneficiary_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
    name TEXT NOT NULL
);
CREATE TABLE funds (
    fund_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
    name TEXT NOT NULL,
    allocation_cents INTEGER NOT NULL CHECK(typeof(allocation_cents)='integer' AND allocation_cents >= 0)
);
CREATE TABLE requests (
    request_id TEXT PRIMARY KEY,
    beneficiary_id TEXT NOT NULL REFERENCES beneficiaries(beneficiary_id),
    fund_id TEXT NOT NULL REFERENCES funds(fund_id),
    purpose TEXT NOT NULL CHECK(length(trim(purpose)) BETWEEN 1 AND 500),
    amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND 1000000000),
    status TEXT NOT NULL CHECK(status IN ('Pending','Approved','Rejected','Paid')),
    created_by_staff_id TEXT NOT NULL REFERENCES staff(staff_id),
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paid_by_staff_id TEXT REFERENCES staff(staff_id),
    paid_at TEXT,
    UNIQUE(created_by_staff_id, idempotency_key),
    CHECK(
        (status='Paid' AND paid_by_staff_id IS NOT NULL AND paid_at IS NOT NULL)
        OR (status<>'Paid' AND paid_by_staff_id IS NULL AND paid_at IS NULL)
    )
);
CREATE INDEX requests_fund_status ON requests(fund_id, status);
PRAGMA user_version = 8;
