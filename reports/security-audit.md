# Security Audit

Threat-oriented review of authentication, authorization/tenant isolation, web
safety, secrets, and system operations. No usable secrets are reproduced here.
Severities: P0 (release stop) · P1 (high) · P2 (medium) · P3 (low).

## Authentication
- Login is email-or-username with `check_password` + `temp_password` fallback,
  status gate, permission-row gate, and a **cache-backed** failure throttle
  (5 attempts / 300 s per IP+username) — `core/views.py:60-113`.
- Session fixation guarded with `request.session.cycle_key()` on login/2FA
  (`core/views.py:34`). `pp_admin`/`pp_2fa`/`pp_brand` cookies are mirrored,
  `HttpOnly`, `SameSite=Lax` (`core/views.py:130-151`).
- 2FA TOTP uses `hmac.compare_digest`, window ±2 (`core/totp.py`).
- `admin_forgot` reduces `reset_limit` and never leaks account existence
  (generic message). ✅

### SEC-2 (P2) — Password hashing not wire-compatible with upstream
`config/settings.py:90-93` lists `BCryptSHA256PasswordHasher` first with a
comment claiming "legacy rows verify". Django's `BCryptSHA256` pre-hashes the
password with SHA-256 before bcrypt, whereas PHP `password_hash()` uses raw
bcrypt. A hash copied from a PHP database therefore **cannot** be verified by the
port, contradicting the comment. If legacy-hash import is a requirement, use
`django.contrib.auth.hashers.BCryptPasswordHasher` (raw bcrypt) first.
Confidence: HIGH. Not changed (auth hashing change without migration decision).

## Authorization / tenant isolation
- `current_admin` requires a live `browser_log` cookie row AND an `active`
  admin (`staff/auth.py:12-33`). `page_required`/`perm_required` decorators
  enforce the same `canAccessPage`/`hasPermission` schema as PHP.
- Admin `role == "admin"` bypasses resource checks (by design, matches PHP).
- Bulk operations checked individually where exercised (transaction, staff,
  gateway, addon bulk) — each performs a `has_permission` check before mutation
  (`staff/views.py`).
- Staff self-edit guard present (`staff/views.py:1944-2010`); note the port
  blocks via redirect+messages whereas upstream `staff-management/edit.php:30-33`
  returns HTTP 403 (documented deviation, `DEV-1`, P3).

## Web / API safety
- CSRF: standard Django middleware active; only `/api/*`, `/ipn/*`, companion,
  `/invoice/webhook/`, `/cron/*` are `csrf_exempt` (correct — they are
  server-to-server or unauthenticated by design).
- SQL injection: all queries go through the ORM. The one raw-SQL surface,
  `core/storage.run_sql()`, is invoked only by the admin-only module import /
  update flow after a `manage_import`/`manage_update` permission check
  (`staff/views.py:2396-2406`). It executes SQL from an uploaded archive — this
  is upstream behaviour (`system-settings-import`) and is gated, but it is
  effectively admin-level RCE by design (`SEC-3`, P2, accepted risk).
- Zip handling: `zipfile.extractall` is used for update/import/addon. CPython's
  `_extract_member` strips `..` components, mitigating Zip-Slip. However, the
  extracted tree is then `copy_folder(root, APP_ROOT)` — i.e. arbitrary files are
  written over the application root and `sql.sql` is executed. Admin-gated; same
  as upstream (`SEC-3`).
- Template output is auto-escaped by Django; no `|safe` on user-controlled
  fields found in the sampled templates.
- SSRF: outbound webhooks go to `row.webhook_url`, which for API-created
  transactions is domain-allowlisted (`api/views.py:_check_domain`). Manual/admin
  paths accept arbitrary URLs (upstream parity). `refresh_currency` fetches a
  fixed CDN host.
- Security headers: `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options:
  nosniff`, `Referrer-Policy` (`core/middleware.py`). No CSP (upstream none).

### SEC-1 (P2) — TLS verification disabled on two call sites
`gateways/api_gateways.py` SSLCommerz calls use `verify=False`, and
`cron_app/tasks.py:refresh_currency` uses `verify=False`. Both mirror upstream,
but they expose merchant traffic / FX data to MITM. Remediation: enable TLS
verification (SSLCommerz sandbox/live both serve valid certs). Confidence: HIGH
(code-level fact); exploitability MEDIUM.

## Secrets & operational security
- `DJANGO_SECRET_KEY` falls back to a **hardcoded** dev value when unset
  (`config/settings.py:7`). `DEBUG` defaults off (good). `ALLOWED_HOSTS` defaults
  to `*` (`config/settings.py:9`). Production hardening is env-gated behind
  `DJANGO_SECURE=1` (`SECURE_SSL_REDIRECT`, secure cookies, CSRF trusted origins).
  **SEC-4 (P2)**: running the port with defaults in production yields a known
  secret key + wildcard hosts. Documented in README only indirectly.
- Gateway credentials are stored in `pp_gateways_parameter.value` as plain text
  (upstream parity — no at-rest encryption). `SEC-5 (P2)`.
- Cron protected by a random `cron-job` env token compared with `!=`
  (`cron_app/views.py`) — not constant-time (`SEC-6`, P3); token is 8 hex bytes
  (`set_env("cron-job", secrets.token_hex(8))`) = 64 bits, adequate.
- Installer: random admin password when `--noinput`; no shipped default
  credentials. ✅

## Confirmed security-relevant defects fixed in this audit
None of the four code fixes were security vulnerabilities per se; the
transaction-`ref` bug (`F-GW-1`) had a **financial-integrity** impact (gateway
callbacks could not match the order, so a valid payment could be left
un-completed and an invalid one could not be matched to an order). See
`findings.json`.

### SEC-7 (P2, upstream defect) — weak companion-device token / no login throttle
`api/companion.py:_login` pairs a device using a **10-digit numeric** OTP
(`M.generate_item_id()`), then rotates the token with the same generator. The
companion `login` endpoint is unauthenticated and has no rate limiting, so the
search space is ~10^10 and online-guessable. This **exactly mirrors** upstream
(`pp-adapter.php:9157-9175`, also `generateItemID()`), so it is recorded as an
`UPSTREAM_DEFECT` accepted for parity; remediation is a roadmap item.

## Residual / unverified
- No SAST/dependency scanner was run (none configured in CI).
- No penetration test of the admin panel or remaining ~48 staff routes.
- MySQL/PostgreSQL deployment not exercised.
