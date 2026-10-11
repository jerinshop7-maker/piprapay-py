# Executive Summary — PipraPay Django port vs official PipraPay

## Verdict
**CONDITIONAL.** The port's source lineage is established and its breadth is
genuine (48 gateways, 23 tables, ~90 routes, 196 tests green). Four real defects
were found and fixed with regression coverage during this audit, one of them
**financial-integrity relevant**. On the strength of code review + unit tests
alone the port is **not** production-ready: no live-provider validation, no
non-SQLite database run, and unresolved P2 security/config items remain.

## Baseline
- Port baseline `3b4f396`; audit fixes committed as `63d4e75` (clean
  tree) · upstream `021ecd3`, tag `v3.0.0`.
- Port targets `v3.0.0-beta` → **same product generation**; a single comparison
  is valid, so no two-generation split was needed.
- At audit start the working tree had 187 tests with **10 failures + 1 error**
  (HEAD alone had 108 green). All were triaged against upstream; all now pass.

## Most important findings
| ID | Sev | What | Status |
|---|---|---|---|
| F-GW-1 | P1 | Checkout passed the wrong transaction dict to gateway plugins (no `ref`/`customer_info`), so API callbacks could not match the order and merchant references were empty. | **Fixed + tested** |
| F-MONEY-1 | P2 | `money_sanitize` dropped the minus sign on negative amounts. | **Fixed + tested** |
| F-MONEY-2 | P2 | `/invoice/webhook/` route raised TypeError (missing `ref`) on every request → 500. | **Fixed + tested** |
| F-GW-2 | P2 | Binance IPN echoed the wrong JSON shape and never completed the transaction. | **Fixed + tested** |
| SEC-2 | P2 | bcrypt config claims PHP-hash compatibility it does not have. | Open (decision needed) |
| SEC-1 | P2 | TLS verification disabled (SSLCommerz, FX fetch). | Open |
| SEC-4 | P2 | Insecure default `SECRET_KEY` / `ALLOWED_HOSTS=*`. | Open |
| SEC-3/SEC-5 | P2 | Admin zip import executes SQL over app root; gateway creds stored in clear. | Open (upstream parity) |
| DOC-1 | P3 | Docs contradict code (legacy-DB reuse claim; stale test counts). | Open |

Full detail: `findings.json` (17 findings: 6 resolved, 11 open), `security-audit.md`, `gateway-audit.md`.

## Real execution results (not assumed)
```
python -m compileall -q .                          exit 0
python manage.py check                             exit 0 (no issues)
python manage.py makemigrations --check --dry-run  No changes detected (exit 0)
python manage.py test tests                        Ran 196 tests — OK
```
Baseline before fixes: 187 tests, 10 failures + 1 error (working tree); 108 OK at HEAD.
This pass added a production-config fail-fast guard (`DJANGO_SECURE=1` now
rejects a default `SECRET_KEY`/wildcard hosts), failure-injection tests
(provider timeout / malformed response leave a transaction `initiated` and emit
no webhook), and mocked provider-branch tests for SSLCommerz, OxaPay and
NOWPayments (success + failure).

## Release risks
1. **No live provider validation** — mocked tests passing does not prove a live
   gateway works (`UNVERIFIED` for the 13 API gateways).
2. **SQLite-only** — MySQL/PostgreSQL and the `utf8mb4` path are untested.
3. **Config defaults** are development-grade; deploying unchanged is unsafe.
4. **Timestamp columns diverge** from upstream, so a legacy DB is not reusable
   without an import/migration step.

## Recommended next steps
1. Fix TLS verification (R5, cheap) and production fail-fast (R7).
2. Decide the password-hashing strategy (R4).
3. Add concurrency + provider-branch + failure-injection tests (R1–R3).
4. Validate against MySQL and a single provider sandbox before staging sign-off.
5. Correct the documentation (DOC-1).

## Recommendation wording
Not "READY". Based on the evidence: **CONDITIONAL — blocked on P2 security/config
items and live/multi-DB validation; safe to continue in staging after R5/R7 and
the R1–R3 tests land.**
