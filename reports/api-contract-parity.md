# API Contract Parity

Public JSON API ported from `index.php` `case 'api'` (`index.php:333-1080`) and
the companion-device API (`pp-adapter.php`). All endpoints are `POST` +
`csrf_exempt`; the port intentionally rejects non-POST with
`INVALID_JSON_PAYLOAD` (`api/views.py:141`), whereas PHP would fall through.

Auth header: `MHS-PIPRAPAY-API-KEY: <key>`, with an added `Authorization: Bearer`
fallback (`api/views.py:34-42`) — a documented superset.

## Endpoint comparison

### POST `/api/checkout/redirect/`
Validation order compared line-by-line against `index.php:402-628`.

| Step | Upstream | Port | Parity |
|---|---|---|---|
| auth key lookup + expiry | yes | yes (`_auth`) | ✅ |
| scope `create_payment` | yes | yes | ✅ |
| `return_url` validity → allowlist → active | `INVALID_URL` | `INVALID_URL` (`_check_domain`) | ✅ |
| `webhook_url` validity → allowlist → active | `INVALID_URL` | `INVALID_URL` | ✅ |
| `metadata` dict / JSON string / else | `INVALID_JSON` / `INVALID_METADATA` | same (`_parse_metadata`) | ✅ |
| `full_name` empty | `MISSING_FIELD` | `MISSING_FIELD` | ✅ |
| email invalid | `INVALID_EMAIL` | `INVALID_EMAIL` | ✅ |
| `mobile` empty | `MISSING_FIELD` | `MISSING_FIELD` | ✅ |
| `amount` `is_numeric` and `>0` | `INVALID_AMOUNT` | `INVALID_AMOUNT` (`_is_numeric`) | ✅ |
| currency exists for brand | `INVALID_CURRENCY` | `INVALID_CURRENCY` | ✅ |
| suspended customer | `INVALID_CUSTOMER` | `INVALID_CUSTOMER` (`_suspend_error`) | ✅ |
| creates transaction + customer, returns `{pp_id, pp_url}` | yes | yes | ✅ |

**Order is significant**: the port validates URLs *before* customer fields,
matching upstream. (An inherited test asserted `MISSING_FIELD` for a request with
an invalid webhook URL; upstream returns `INVALID_URL`. Test corrected — see
`F-API-1`.)

### POST `/api/checkout/popup/`
Same as redirect minus `return_url` (defaults `--`), matching `index.php:630-777`. ✅

### POST `/api/verify-payment/`
Scope `verify_payment`; `pp_id` required (`INVALID_PP_ID`); returns the 16-field
payload with `round_total=True` (`api/views.py:244-257`, `core/transactions.py:59-107`).
Upstream `full_name` defaults `N/A` on missing key **only**; the port uses the
same `_or_na` (missing→`N/A`, explicit null→`N/A`). ✅

### POST `/api/refund-payment/`
Scope `refund_payment`; `INVALID_PP_ID`, then `INVALID_STATUS` unless
`completed`; bKash-tokenized branch appends `Refund TrxID/Amount/Time` to
`source_info` **only when a refund field is present**, then sets `refunded`. ✅
Refunds for non-bKash API gateways are **not** implemented upstream either
(the port returns `refunded` after marking status — same observable behaviour as
`index.php:860-1000`).

### Unknown `/api/<x>/`
400 `INVALID_JSON_PAYLOAD`, matching PHP's unknown `api_type`. ✅

## Companion device API (`/api/companion/<action>/`)
`login` (OTP), `account-information`, `sms-transmit-bulk`, `sms-transmit-sender`,
`delete-sms-data`. Invalid/missing token → `{"status":"false"}`. Covered by
`tests/test_coverage.py::test_companion_invalid_tokens`. ✅

## Residual / unverified
- Live provider HTTP is **not** exercised; only mocked. Provider-side contracts
  remain `UNVERIFIED` (see gateway-audit.md).
- `metadata` sentinel handling for `null` (JSON `null`) is parsed as `None` →
  `INVALID_METADATA`, matching PHP's `is_array`/`is_string` else-branch. Verified
  by reading both sources; not separately tested (TEST-GAP).
