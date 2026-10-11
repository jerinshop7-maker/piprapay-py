# Test Coverage & Execution Results

All commands were run in `/app/piprapay-django` with Python 3.14.7 /
Django 5.2.18. Results below are the **actual** captured outputs.

## 1. Baseline at HEAD (`3b4f396`, clean checkout)

```
$ python manage.py test tests          # detached worktree at HEAD
Ran 108 tests in 19.450s
OK
```

## 2. Baseline of the dirty working tree (audit start)

```
$ python -m compileall -q .        -> exit 0
$ DJANGO_DEBUG=0 python manage.py check
System check identified no issues (0 silenced).   -> exit 0
$ python manage.py test tests
Ran 187 tests in 50.019s
FAILED (failures=10, errors=1)
```

The 11 failures were introduced by the prior, uncommitted parity pass (108 → 187
tests). Each was triaged against upstream source (not "made green"):

| Test | Root cause | Resolution |
|---|---|---|
| `test_money_edges` | real bug: `money_sanitize` dropped the sign | fixed code (`core/money.py:33`) |
| `test_stripe/aamarpay/eps/pathaopay/paypal_callback` | real bug: plugin data lacked `ref`; EPS/PayPal mock verbs wrong | fixed code + mock verbs |
| `test_invoice_webhook_bad_json` | real bug: `/invoice/webhook/` route passed no `ref` | fixed URL (`checkout/urls.py:9-10`) |
| `test_binance_ipn_mocked` | real parity bug: wrong JSON shape + IPN never completed | fixed code + expectation |
| `test_double_verify_second_rejected` | wrong expectation: upstream returns Code 101 first | corrected test, added dup-case |
| `test_api_scope_currency_and_fields` | wrong expectation: upstream validates webhook URL first | corrected test |
| `test_staff_edit_self_blocked` | test seed lacked `staff_management` permission | corrected seed |

## 3. Final state (after fixes)

```
$ python -m compileall -q .                              -> exit 0
$ python manage.py check                                 -> exit 0 (no issues)
$ python manage.py makemigrations --check --dry-run      -> No changes detected (exit 0)
$ python manage.py test tests
Ran 205 tests in 54.075s
OK
```

All 205 tests pass; migration set is complete. (187 after the parity fixes,
+9 new tests added in this audit: failure-injection ×2, production-settings ×1,
and mocked SSLCommerz / OxaPay / NOWPayments provider branches ×6, plus
+9 UI regression tests in `tests/test_ui.py` from the gap-analysis pass below.)

## 3b. Gap-analysis pass (post-UI): 3 new findings, 9 new tests

A source-level re-audit after the full-UI pass (41 PHP admin files vs 44
Django staff URLs; 7 twenty-six theme files vs 8 checkout templates; every
POST form re-checked for CSRF; every new `|safe`/JS interpolation reviewed)
found three genuine defects, all fixed with regression coverage:

- **UI-1 (P1):** gateway verify forms had no CSRF token while
  `verify_endpoint` is not exempt — real browsers 403 every payer Verify.
  Fixed (`gateway.html` ×3 forms); proven by
  `test_verify_endpoint_enforces_csrf` with an `enforce_csrf_checks` client.
- **UI-2 (P3 parity):** no `/admin/brand-setting/` hub (upstream
  `brand-setting/index.php`). Added view + route + template + sidebar link;
  proven by hub render + per-card permission tests.
- **UI-3 (P3):** unquoted `escapejs` URL broke the local 2FA QR fallback.
  Fixed; covered by the my-account render sweep.

`tests/test_ui.py` (9 tests): 29-page admin render sweep with shell markers,
hub permission gating, dashboard chart-lib ordering, checkout theme markers,
verify CSRF round-trip, and static-asset/WhiteNoise assertions.

## 4. Tests added / corrected in this audit

Corrected (all in `tests/test_coverage.py`):
- `CheckoutBranchTests.test_eps_callback_valuea` — mock `GET` for the status
  check, `POST` for the token (matches `eps/class.php:195`).
- `CheckoutBranchTests.test_paypal_callback_mocked` — mock
  `requests.request` for order/capture.
- `CheckoutBranchTests.test_binance_ipn_mocked` — assert upstream
  `{status:"true", title, message}` and transaction completion.
- `CheckoutBranchTests.test_double_verify_second_rejected` — assert Code 101 on
  re-verify, then a **new** initiated txn reusing the trxid to prove the dup guard.
- `MiscWorkflowTests.test_api_scope_currency_and_fields` — assert `INVALID_URL`
  for a bad webhook, plus a dedicated `MISSING_FIELD` case.
- `StaffPostTests.test_staff_edit_self_blocked` — grant `staff_management` +
  `staff:edit` so the self-edit guard (not the page gate) is exercised.

No new test file was added; the fixes are pinned by the corrected/added cases
above, which now fail on the pre-fix code and pass on the fixed code.

## 5. Quality assessment / untested areas

Strengths: deterministic fixtures, no live providers, real ORM assertions.

Gaps (each `TEST-GAP` in `findings.json`):
- No concurrency test for `set_transaction_status` (row-lock path) under
  duplicate callbacks.
- No live test (mocked only) for any gateway; SSLCommerz/OxaPay/NOWPayments
  branches are now mocked-tested, but refund-failure, payment-link default flow
  and companion SMS ingest happy-path remain untested.
- No MySQL/PostgreSQL run; only SQLite.
- No legacy-PHP-hash verification test.
- No tests for `SEC-4` defaults or cron token comparison.
- Failure-injection (provider timeout, malformed provider response, DB
  exception mid-transaction) is only partially covered.
