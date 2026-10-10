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
python manage.py migrate
python manage.py createsuperuser          # optional; staff login uses core.Admin instead
python manage.py run_cron                 # cron step (or GET /cron/<token>/)
python manage.py test tests               # parity suite (must stay green)
python manage.py runserver 0.0.0.0:8080
```

MySQL (legacy reuse): set `DB_ENGINE=django.db.backends.mysql DB_NAME=.. DB_USER=.. DB_PASSWORD=.. DB_HOST=.. DB_PORT=..` + `pip install mysqlclient`.

## 4. Conventions (do not break)

- **Money**: `Decimal`, 8 places internally (`core/money.py` = PHP `bcscale(8)`). Display via `money_round(x)` (2 dp). Never use float for amounts.
- **`'--'` sentinel** means empty (DB defaults, URLs, images). Preserve it; don't "clean" to `None`/`""` on write paths.
- **UTC everywhere** (`USE_TZ=True`); user-TZ only at render via `convert_utc_to_user_tz`.
- **IDs**: `generate_item_id()` (secrets-based) for `ref`/`a_id`/`gateway_id`; `pp_id` is 27 chars.
- **Passwords**: Django `make_password`/`check_password` (PHP used `password_hash`; re-hash on first login is acceptable, never roll custom crypto).
- **2FA**: stdlib TOTP in `core/totp.py` — no new deps.
- **Hooks**: `core/hooks.py` (`do_action`/`apply_filters`); cron and transactions emit `transactions.updated`, `system.update.available`. Keep emitting.
- **Gateway plugins**: add new gateway by adding a `SPECS` entry in `gateways/plugins.py` (slug key) — registry auto-exposes it. API gateways subclass and override `process_payment`/`callback`/`ipn` with `requests` (never `urllib` directly in plugins).
- **Permissions**: never rename keys in `core/permissions.py:PERMISSION_SCHEMA` (17 resources / 15 pages) — staff UI depends on them.
- **SMS parsing**: regexes in `core/sms_engine.py:FORMATS` are priority-ordered per provider; add specific patterns above generic ones.
- **Templates**: checkout pages mirror `twenty-six/*.php` filenames (`checkout.html`, `gateway.html`, `status.html`, `invoice.html`, `payment_link.html`).

## 5. Definition of done for any change

1. `python manage.py check` passes.
2. `python manage.py test tests` passes (extends parity tests if you add behaviour).
3. No new third-party dependency without updating `requirements.txt` + this file.
4. Any new PHP-parity behaviour gets a row in `README.md` comparison table.

## 6. Gotchas from the PHP original

- Transaction flow is `initiated → pending → completed` (automation/manual) or `initiated → completed` (API/callback). `set_transaction_status()` only transitions out of `initiated` — keep that guard.
- `verify-payment`/`refund-payment` require `verify_payment`/`refund_payment` scopes; refund only from `completed`; bKash-tokenized does a live refund call first.
- Return/webhook URLs must be whitelisted in `pp_domain` or the API returns `INVALID_URL` — same here.
- Cron uses a file lock (`media/storage/cron.lock`, 10 min) and echoes JSON immediately in PHP; here `run_cron()` returns the dict and the view serialises it.
- `pp_downloadReceiptPDF` is a printable receipt here (`/payment/<id>/receipt/`); wire a real PDF lib only if asked.
