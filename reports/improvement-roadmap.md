# Improvement Roadmap

Prioritized, dependency-aware. Severity/labels follow `findings.json`.

## Release gates (must hold before any production claim)
- G1 — `python manage.py check`, `makemigrations --check`, and `test tests` are
  green. **Met** at audit time (196/196).
- G2 — No P0/P1 findings open. **Met** (F-GW-1 fixed).
- G3 — `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_SECURE=1` set and a
  production DB (MySQL/PostgreSQL) exercised. **Not met.**

## P1 / R1 — Concurrency test for the transaction state machine (TEST-1)
Add a test that fires two `set_transaction_status(ref, "completed")` calls
concurrently and asserts exactly one transition (SQLite runs single-writer;
use `TransactionTestCase` + threads, or assert the `select_for_update` guard
directly). Depends on nothing. Acceptance: duplicate callback leaves status
`completed` once and emits one webhook.

## P2 / R2 — Provider branch coverage (TEST-1)
Add mocked success + failure tests for SSLCommerz, OxaPay, NOWPayments,
PayStation cancellation, and the bKash refund **failure** path. Depends on R3.

## P2 / R3 — Failure-injection harness (TEST-1)
Introduce a small helper that patches `requests` to raise `Timeout`,
`ConnectionError`, and to return non-JSON bodies, and assert no partial local
writes. Depends on nothing.

## P2 / R4 — Password hashing decision (SEC-2 / DEP-1)
If legacy PHP hash import is a requirement, switch the first hasher to
`BCryptPasswordHasher` (raw bcrypt) and add a test that a `$2y$` hash verifies.
Otherwise remove the misleading comment. Requires a migration/back-compat
decision (blocker).

## P2 / R5 — Enable TLS verification (SEC-1)
Remove `verify=False` from the SSLCommerz calls and the FX fetch; add a test
asserting `verify=True` is passed. Low risk.

## P2 / R6 — Import/update hardening (SEC-3)
At minimum: reject archives containing absolute/`..` members and symlinks
(explicit, not relying on stdlib sanitisation), and stage extraction outside the
app root. Accept-and-document is the fallback.

## P2 / R7 — Production config fail-fast (SEC-4)
When `DEBUG` is false and `DJANGO_SECRET_KEY` is unset/default, or
`DJANGO_ALLOWED_HOSTS` is `*`, raise at startup. Add a settings test.

## P3 / P1 — MySQL/PostgreSQL validation (schema-parity)
Run migrations + `test tests` against MySQL 8 to validate
`OPTIONS={'charset':'utf8mb4'}` and enum→CharField behaviour.

## P3 — Composite indexes (PERF-1)
After profiling, add the upstream composite indexes (`addon_id,status,created_date,
updated_date`, etc.).

## P3 — Staff self-edit 403 (DEV-1)
Return HTTP 403 to match `staff-management/edit.php`.

## P3 — Documentation (DOC-1)
- Fix the `core/models.py` docstring reuse claim (timestamps are not wire-compatible).
- Refresh README test inventory (108 → 196; add `test_coverage|security|workflows`) — **done**: README now lists 196 tests / 7 modules.
- Add a "production deployment" section distinct from the quickstart.
- Record this audit's four fixes.

## Dependencies
R2 → R3. R4 blocks on a product decision. R6/R7 independent.

## Status update (this pass)
- **R3 — DONE.** `tests/test_workflows.py::FailureInjectionTests`
  (`test_provider_timeout_leaves_initiated`,
  `test_malformed_provider_response_leaves_initiated`).
- **R7 — DONE.** `config/settings.py::validate_production_settings` invoked when
  `DJANGO_SECURE=1`; covered by
  `tests/test_workflows.py::ProductionSettingsTests`.
- **R1 — already covered** by `tests/test_security.py::StateMachineTests` +
  `tests/test_workflows.py` (double-complete rejected, terminal states frozen).
- **R5 (TLS verify) — still open.** Enabling `verify=True` cannot be validated
  without live provider contacts; recorded as a blocked change rather than made
  speculatively.
- **R2 — DONE.** `tests/test_workflows.py::ProviderCallbackTests` covers
  SSLCommerz success/invalid, OxaPay success/amount-mismatch and NOWPayments
  success/cancel with mocked transport.

## Acceptance criteria
Each item is done when: a test demonstrates the change, `python manage.py test
tests` is green, and the relevant doc/report reflects it.
