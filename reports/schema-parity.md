# Schema Parity

Upstream reference: `pp-content/pp-install/db.sql` (23 tables, MySQL/InnoDB,
`utf8mb4_general_ci`). Port: `core/models.py`, `db_table = "pp_*"` preserves the
legacy names.

## Table coverage

All 23 upstream tables are represented 1:1:
`pp_addon`, `pp_addon_parameter`, `pp_admin`, `pp_api`, `pp_balance_verification`,
`pp_brands`, `pp_browser_log`, `pp_currency`, `pp_customer`, `pp_device`,
`pp_domain`, `pp_env`, `pp_faq`, `pp_gateways`, `pp_gateways_parameter`,
`pp_invoice`, `pp_invoice_items`, `pp_payment_link`, `pp_payment_link_field`,
`pp_permission`, `pp_sms_data`, `pp_transaction`, `pp_webhook_log`.

Verified by diffing the `CREATE TABLE` names in `db.sql` against
`Model._meta.db_table` for every `core` model.

## Column / type comparison

| Aspect | Upstream | Port | Assessment |
|---|---|---|---|
| PK | `id int(11)` auto | Django `BigAutoField` default | compatible |
| money columns | `decimal(20,8)` | `DecimalField(max_digits=20, decimal_places=8)` | ✅ exact |
| `status`/enum columns | MySQL `enum(...)` | `CharField(max_length=n)` | value-set preserved, no DB-level enum — accepted deviation |
| `created_date`/`updated_date` | `varchar(20)` `Y-m-d H:i:s` | `DateTimeField` (`auto_now_add`/`auto_now`) | **divergence** — see below |
| `pp_admin.2fa_secret` | `varchar(20)` | `CharField(max_length=64)` | widened (safe) |
| `pp_admin.2fa_status` | column `2fa_status` | `db_column="2fa_status"` | ✅ |
| `pp_transaction.ref` | `varchar(30)` | `CharField(max_length=30) unique` | ✅ |
| `pp_env` unique key | unique `(brand_id, option_name)` | `unique_together` | ✅ |
| indexes | composite keys per table | matching `models.Index` entries | ✅ (see note) |

### Timestamp-type divergence (material)
Upstream stores timestamps as text (`varchar(20)`, UTC, `Y-m-d H:i:s`).
The port uses `DateTimeField` with `USE_TZ=True`. Consequences:
1. A **pre-existing** upstream MySQL database is **not** directly usable — the
   model docstring's claim that it can be "reused without migration of data"
   (`core/models.py:4-8`) is **incorrect** and should be corrected
   (finding `DOC-1`). `gaps.md` separately says legacy DBs "need the documented
   import step", contradicting the docstring.
2. String comparison/`LIKE` filters on date columns available upstream are not
   directly equivalent; the port must use ORM date lookups.

### Index note
Upstream declares many composite index keys (e.g. `pp_addon (addon_id, status,
created_date, updated_date)`). The port models them as separate single-column or
smaller indexes. Query-planner behaviour may differ under load; no correctness
impact (finding `PERF-1`, low).

## Migrations
- `core/migrations/0001_initial.py` — full schema.
- `core/migrations/0002_addon_pp_addon_slug_6f59b6_idx_and_more.py` (untracked/new)
  — adds an index on `pp_addon.slug`.
- `python manage.py makemigrations --check --dry-run` → **No changes detected**
  (exit 0). The migration set is complete for the current models.
- Migrations applied cleanly against a fresh SQLite test DB (test run output).

## Not tested
- No MySQL/PostgreSQL run (only SQLite was exercised). The port's `utf8mb4`
  MySQL `OPTIONS` path is **unverified**.
- Legacy-data import round-trip is **not** covered by a test (TEST-GAP).
