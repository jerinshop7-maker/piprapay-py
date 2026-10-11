# AGENTS.md — PipraPay Django Port (`piprapay-django/`)

> Port of PHP original at `../PipraPay` (single-entry `index.php` + `pp-content/`).
> This file is the operating manual for AI coding agents working in this repo.
> Read it before changing anything.

## 1. What this is

End-to-end Django 5 port of **PipraPay** — self-hosted payment-automation platform
(AGPL-3.0). 100% functional comparison: every PHP route, table, gateway plugin,
admin page, API error code, SMS parser, cron step and theme string exists here
under the same names/slugs.

## 2.Repo map (PHP → Django)

| PHP original | Django port | Notes |
|---|---|---|
| `index.php` router (`?page=` switch) | `config/urls.py` | Same slugs: `payment/`, `invoice/`, `payment-link/`, `admin/`, `cron/`, `ipn/`, `api/` |
| `pp-content/pp-include/pp-functions.php` | `core/money.py`, `core/env.py`, `core/permissions.py`, `core/hooks.py`, `core/transactions.py`, `core/sms_engine.py`, `core/webhooks.py`, `core/totp.py` | Split by concern; function names snake_cased (`money_add`, `set_transaction_status`, `mfs_message_verified`, `send_ipn_multi`) |
| `pp-content/pp-include/pp-adapter.php` | `config/settings.py` + `core/middleware.py` + `core/views.py` | Path slugs in `PIPRAPAY_PATHS`; security headers in middleware; session/auth in views |
| `pp-content/pp-install/db.sql` (23 tables) | `core/models.py` (23 models, same `db_table = pp_*`) | Point `DB_*` env at MySQL to reuse legacy DB |
| `pp-content/pp-modules/pp-gateways/*/class.php` (48) | `gateways/plugins.py` + `gateways/base.py` + `gateways/registry.py` | Same slugs/titles/types; `GatewayBase` = PHP class contract |
| `pp-content/pp-modules/pp-themes/twenty-six/` | `themes/twenty_six.py` + `templates/checkout/*` | Same 9 fields, 5 languages |
| `pp-content/pp-admin/pp-root/*` (≈30 pages) | `staff/views.py` + `staff/urls.py` + `templates/staff/*` | Same page names; `staff/auth.py` = login/permission guards |
| API `api/checkout/*`, `verify`, `refund` | `api/views.py` + `api/urls.py` | Identical error codes |
| `ipn/<gateway_id>` | `gateways/ipn_views.py` | Identical INVALID_GATEWAY behaviour |
| cron (`cron/<token>`) 6 steps | `cron_app/tasks.py` + `views.py` + `management/commands/run_cron.py` | Same order: lock → update-check → pending-verify → FX → balances → webhooks |
| `pp-config.php` + `pp_env` | `config/settings.py` + `core/env.py:get_env/set_env` | `brand_id='both'` = global scope |

## 3. Commands

```bash
pip install -r requirements.txt
python manage.py check_requirements  # mirrors pp-requirement.php table
python manage.py migrate
python manage.py collectstatic --noinput  # WhiteNoise serves /static/ from staticfiles/
python manage.py pp_install          # mirrors pp-install wizard (admin+brand+BDT+permission+cron key)
python manage.py createsuperuser          # optional; staff login uses core.Admin instead
python manage.py run_cron                 # cron step (or GET /cron/<token>/)
python manage.py test tests               # parity suite (must stay green)
python manage.py runserver 0.0.0.0:8080
```

MySQL (legacy reuse): set `DB_ENGINE=django.db.backends.mysql DB_NAME=.. DB_USER=.. DB_PASSWORD=.. DB_HOST=.. DB_PORT=..` + `pip install mysqlclient`.

## 4. Conventions (do not break)

- **Money**: `Decimal`, 8 places internally (`core/money.py` = PHP `bcscale(8)`).
  Display via `money_round(x)` which **truncates** like bc (10.005 -> "10.00",
  NOT half-up). `money_sanitize` is `is_numeric`-only (commas rejected).
  Never use float for amounts.
- **`'--'` sentinel** means empty (DB defaults, URLs, images). Preserve it; don't "clean" to `None`/`""` on write paths.
- **UTC everywhere** (`USE_TZ=True`); user-TZ only at render via `convert_utc_to_user_tz`.
- **IDs**: `generate_item_id()` = **digits only** via secrets (mirrors PHP
  `mt_rand(0,9)` loop); `pp_id` is 27 digits.
- **Tolerance**: `verify_payment_tolerance` accepts paid in
  `[checkout, checkout+tolerance]` with both > 0 — underpayments never pass.
- **Passwords**: Django `make_password`/`check_password` (PHP used BCRYPT
  `password_hash`); `temp_password` fallback login + `reset_limit` + the
  `forgot.password` hook are ported in `core/views.py`.
- **2FA**: stdlib TOTP in `core/totp.py` — no new deps. Window default 2
  (±60s) mirrors `verifyCode(...,2)`.
- **API auth**: key comes from the `MHS-PIPRAPAY-API-KEY` header (PHP
  `getAuthorizationHeader()`); `Authorization`/`Bearer` is a fallback only.
- **Hooks**: `core/hooks.py` (`do_action`/`apply_filters`); cron and transactions emit `transactions.updated`, `system.update.available`. Keep emitting.
- **Gateway plugins**: same slugs/titles/types; `GatewayBase` = PHP class contract.
  Config specs live in `gateways/plugins.py:SPECS` (colors/fields verified
  against source). Verbatim language strings + instruction steps live in
  `gateways/lang_data.json` (regenerate via `tools/extract_gateway_lang.py`).
  Real HTTP for the 13 API gateways lives in `gateways/api_gateways.py`
  (use `requests`, never `urllib` directly in plugins). Nagad RSA uses
  `cryptography`; image uploads use `Pillow` (all in requirements.txt). Static
  assets are served by `WhiteNoise` (`whitenoise.middleware.WhiteNoiseMiddleware`
  + `CompressedStaticFilesStorage`); re-run `collectstatic` after changing
  `static/` or `templates/` asset references.
- **Permissions**: never rename keys in `core/permissions.py:PERMISSION_SCHEMA` (17 resources / 15 pages) — staff UI depends on them.
- **SMS parsing**: exact PHP regexes in `core/sms_engine.py:FORMATS`
  (telecash/ipay have none — always False, like PHP); output keys are
  `mfs/type/raw` + mapped keys verbatim. `reconcile_by_longest_chain` is the
  full BFS algorithm, not a shortcut.
- **Templates**: checkout pages mirror `twenty-six/*.php` filenames (`checkout.html`, `gateway.html`, `status.html`, `invoice.html`, `payment_link.html`).
- **Known wire deviations** (rules identical, transport differs): staff panel
  and checkout forms POST HTML instead of PHP's AJAX `action=` JSON; receipt
  is printable HTML instead of FPDF; cron responds after work with the same
  `{status:"true",message}` shape; install is `pp_install` instead of the web
  wizard; `pp_demo_mode` gates are not ported.

## 5. Definition of done for any change

1. `python manage.py check` passes.
2. `python manage.py test tests` passes (extends parity tests if you add behaviour).
3. No new third-party dependency without updating `requirements.txt` + this file.
4. Any new PHP-parity behaviour gets a row in `README.md` comparison table.
5. If the change clears a `gaps.md` item, delete that item's checkbox line
   from `gaps.md` (see §7) — never leave a fixed gap listed as open.

## 7. Gap workflow (`gaps.md`)

- `gaps.md` is the single source of truth for remaining PHP↔Django
  divergences, grouped by area (Router, API, Money, Render, Auth, Admin,
  Gateways, Themes). Each item has an ID (`G<area>-<nnn>`), the PHP
  `file:line`, the Django `file:line`, and a severity.
- **Before fixing**: re-read the cited PHP lines yourself. Two past audits
  contained refuted claims (see `gaps.md` → Refuted); never trust an ID
  blindly. If the claim is wrong, move it to Refuted with evidence instead
  of "fixing" it.
- **To clear a gap**:
  1. Fix the code to match PHP (or record a deliberate deviation with
     justification in code comments + `README.md`).
  2. Add or extend a test in `tests/` that fails before and passes after.
  3. Run the full suite green.
  4. **Delete the gap's `- [ ]` line from `gaps.md`.** If the fix only
     partially closes it, rewrite the line to describe exactly what
     remains instead of deleting.
  5. Append one line to the Cleared log at the bottom of `gaps.md`.
- **To add a gap** (new divergence found): append `- [ ] **G<area>-<next>**`
  under the right area with PHP ref, Django ref, one-line description,
  severity. Keep IDs stable — never renumber existing ones.
- Priority order for clearing: BLOCKERs in Area 8 (money/security verify
  paths) first, then Areas 2/7 (API/admin behavior), then Area 10 (payer
  UI), MINORs last.
- Verified permanent decisions live in `gaps.md` → Permanent design
  deviations (no checkboxes) instead of as open items. Graduate an item
  there — with its `ex Gx-xxx` tag and justification — only after proving
  zero observable difference or a deliberate, documented decision. Never
  re-add a checkbox for a graduated item unless the decision changes.

## 6. Gotchas from the PHP original

- Transaction flow is `initiated → pending → completed` (automation/manual) or `initiated → completed` (API/callback). `set_transaction_status()` only transitions out of `initiated` — keep that guard.
- `verify-payment`/`refund-payment` require `verify_payment`/`refund_payment` scopes; refund only from `completed`; bKash-tokenized does a live refund call first.
- Return/webhook URLs must be whitelisted in `pp_domain` or the API returns `INVALID_URL` — same here.
- Cron uses a file lock (`media/storage/cron.lock`, 10 min) and echoes JSON immediately in PHP; here `run_cron()` returns the dict and the view serialises it.
- `pp_downloadReceiptPDF` is a printable receipt here (`/payment/<id>/receipt/`); wire a real PDF lib only if asked.
- `pp_gateways()` prices every gateway in a tab with the FIRST row's fee/discount
  (`response[0]` quirk) — mirrored intentionally in `_gateway_list`.
- `?gateway=` takes a `gateway_id` (not the slug), like the theme templates.
- Cron webhook/hook payloads carry the live `completed` status (PHP sent the
  stale pre-update `pending` — treated as a PHP bug, not mirrored).
- `update.sql`/`sql.sql` runners execute verbatim; MySQL-only statements will
  fail on SQLite — same as running them by hand.
- `tools/extract_gateway_lang.py` regenerates `gateways/lang_data.json` from the
  PHP source when gateway text changes upstream.
