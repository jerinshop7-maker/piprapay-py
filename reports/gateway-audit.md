# Gateway Audit

48 gateways discovered dynamically from `gateways/plugins.py:SPECS`
(not from the README): **13 `api`, 30 `automation`, 5 `manual`**. Cross-checked
against the 48 `pp-content/pp-modules/pp-gateways/<slug>/class.php` directories
upstream.

## Method
For each API gateway the bespoke port in `gateways/api_gateways.py` was read
against the corresponding upstream `class.php` `process_payment()` /
`callback()` / `ipn()` / `refund()`. Request verb, headers, timeout, TLS verify,
response validation, reference/amount matching and duplicate handling were
checked. Only **mocked** transport was exercised in tests — no live provider.
Therefore provider-server contracts remain **UNVERIFIED** even where the local
mock test passes.

## Cross-cutting findings

1. **Transaction dict mismatch (F-GW-1, P1).** `checkout_payment` passed
   `transaction_payload(txn)` (keys `pp_id`, `amount`, …) to gateway classes,
   but the ported classes read `data["transaction"]["ref"]` and
   `["customer_info"]` exactly like PHP's raw DB row. Eight callbacks and every
   `process_payment` that embeds the reference were therefore operating on
   empty refs. **Fixed**: `checkout/views.py:238-240` now adds `ref` and
   `customer_info`. Regression: `tests/test_coverage.py::CheckoutBranchTests`
   (stripe/aamarpay/eps/pathaopay/paypal).
2. **EPS status check verb.** Port uses `GET` for
   `CheckMerchantTransactionStatus` (matches `eps/class.php:195`, which builds a
   query URL with no `CURLOPT_POST`). Inherited test mocked `POST`; corrected.
3. **PayPal HTTP plumbing.** Port routes order-fetch/capture through
   `requests.request(...)`; tests mocked `requests.get/post`; corrected to mock
   `requests.request`.
4. **Binance IPN response shape (F-GW-2, P2).** Port echoed
   `{status, visible_number}`; `binance-personal/class.php:329-331` echoes
   `{status, title, message}` and completes the transaction inside `ipn()`.
   Port's IPN view returned the JSON **before** applying `verified`, so the
   transaction was never completed. **Fixed**: `gateways/ipn_views.py` completes
   then echoes JSON; `api_gateways.py` Binance IPN now returns the upstream
   `status/title/message` shape. Regression:
   `test_coverage.py::CheckoutBranchTests::test_binance_ipn_mocked`.

## API gateway matrix

| slug | verb/verb-callback | auth | TLS | ref/amount check | refund | mock test | parity |
|---|---|---|---|---|---|---|---|
| stripe | POST create; GET session+intent | secret_key basic | on | `metadata.invoice_id == ref` | no | ✅ | ✅ |
| sslcommerz | POST gwprocess; GET validator | store id/pass | `verify=False` (upstream parity) | `value_a == ref`, status VALID | no | — | ✅/TLS-1 |
| oxapay | POST invoice; GET payment | `merchant_api_key` header | on | amount equality + ref | no | — | ✅ |
| nowpayments | POST payment; IPN HMAC | api key | on | tksort HMAC + confirmed | no | — | ✅ |
| aamarpay | POST jsonpost; GET trxcheck | store_id/sig | on | `status_code==2` + `opt_a==ref` | no | ✅ | ✅ |
| bkash-api-tokenized | POST grant/create/execute | app key/secret | on | `-BP-` ref split | ✅ | ✅ | ✅ |
| eps | POST token/init; GET status | HMAC sha512 header | on | `ValueA==ref`, Status Success | no | ✅ | ✅ |
| nagad-merchant-api | POST init/complete (RSA) | private/public key | on | `paymentReferenceId` map | no | ✅ | ✅ |
| pathaopay-merchant-api | POST request/capture | App-Key/Secret | on | `merchant_reference_id` `-BP-` split | no | ✅ | ✅ |
| paypal-api | POST order/capture, GET order | OAuth client creds | on | custom_id/invoice_id/reference_id + ±0.01 | no | ✅ | ✅ |
| paystation | POST status | merchant id/password | on | nested `trx_status` | no | ✅ | ✅ |
| shurjopay | POST token/verify + IPN | user/pass | on | `customer_order_id` `-BP-` split | no | ✅ | ✅ |
| binance-personal | signed GET pay/transactions (IPN) | api/secret | on | orderId + USDT + expected amount | no | ✅ | ✅ (fixed) |

## Automation / manual gateways (30 / 5)
No HTTP; checkout renders instructions + the same-URL verify form
(`sender/trxid/slip`), duplicate-trxid guard, `pending_payment` mobile flow and
slip upload (≤5 MB). Registration data (`SPECS`) and verbatim language strings
(`gateways/lang_data.json`) were regenerated from source by
`tools/extract_gateway_lang.py`. `telecash` and `ipay` intentionally have **no**
SMS formats upstream (mirrored).

## Not verified / risks
- No provider sandbox credentials were used; **live parity is UNVERIFIED**.
- SSLCommerz `verify=False` and the cron FX fetch `verify=False`
  (`cron_app/tasks.py`) mirror upstream but disable TLS verification (`SEC-1`).
- Refund support only exists for bKash-tokenized (upstream parity).
- Binance IPN no longer attaches `source_info` (upstream `pp_set_transaction_status`
  is called without `$moreinfo`, leaving the default `--`).

