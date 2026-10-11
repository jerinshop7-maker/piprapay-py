# gaps.md — Open Parity Gaps (PHP original vs Django port)

10 audit agents compared the PHP original (`../PipraPay`) against this port
file-by-file, line-by-line. Every agent claim below was re-verified against
source before being recorded here.

**Workflow (mandatory):** when you clear a gap — fix the code, add/extend a
test proving it, run `python manage.py test tests` green — **delete that gap's
checkbox line from this file**. See `AGENTS.md §7` for the full workflow.

Legend: `BLOCKER` = user-visible behavioral difference. `MINOR` = cosmetic,
edge-case, or message-text-only difference.

---

## Area 1 — Router, guards, IPN entry, cron entry (`index.php` 1-330, 1730-2110)

- [ ] **G1-010** — Extra URL segments (`ipn/X/Y/`) 404 in Django; PHP silently
  ignores segments[2+]. Accepted divergence (Django convention is safer).
  _(MINOR)_
- [ ] **G1-011** — Maintenance middleware exempts `/admin/system-settings/`;
  PHP has no exemption. Accepted: the exemption is required to ever disable
  maintenance without SSH. _(MINOR)_
  plugin's `supported_languages`/`lang_text` hooks. Open: no plugin consumes
  `lang` on the IPN path today, so no behavioral effect yet.
  `gateways/ipn_views.py:60-76`. _(MINOR)_
- [ ] **G1-029** — Custom `$path_admin` slugs are not honored (Django fixes
  `admin/`; `PIPRAPAY_PATHS` is reserved for future use). Accepted. _(MINOR)_
- [ ] **G1-032** — Cron bad-token body is JSON `{"error":"not found"}` (404);
  PHP serves the HTML 404 page. Accepted wire deviation. _(MINOR)_
- [ ] **G1-033** — Lock file lives at `media/storage/cron.lock`, not
  `pp-media/storage/cron.lock`. Accepted (Django layout). _(MINOR)_
- [ ] **G1-035** — Cron responds after work completes; PHP echoes instantly
  before work. Accepted (documented in `AGENTS.md`). _(MINOR)_
- [ ] **G1-036** — Manifest fetch failures are swallowed; PHP would fatal.
  Accepted (robustness improvement). _(MINOR)_
- [ ] **G1-043** — Cron webhook/hook payloads carry live `completed` status;
  PHP sends stale pre-update `pending`. Intentional PHP-bug fix. _(MINOR)_
- [ ] **G1-045** — `webhook_log.ref` truncated to 15 chars; PHP inserts the
  full 27-char ref (column is `varchar(15)`, so MySQL truncates/errors —
  the slice matches the effective stored value). _(MINOR)_
- [ ] **G1-052** — `homepageRedirect` issues a 302 to `/login/` (or the
  configured host); PHP echoes a JS redirect (XSS-able). Accepted. _(MINOR)_

## Area 2 — Public API + checkout/invoice/link routes (`index.php` 332-1730)

- [ ] **G2-002** — API requires POST; PHP has no method check. Accepted
  (POST-only API is standard). _(MINOR)_
- [ ] **G2-018** — `pp_url` is built from the Host header (`/payment/<id>`);
  PHP uses `$site_url.$path_payment` config. Diverges on subdirectory
  installs / renamed path slugs. _(MINOR)_
- [ ] **G2-032** — Non-`initiated` checkouts render `status.html` with the
  API payload shape instead of the theme `checkout-status.php` flow.
  Functional equivalent; accepted template-level deviation. _(MINOR)_
- [ ] **G2-034** — `?gateway=`/verify/callback flow is inline in
  `checkout/views.py` rather than theme/adapter files. Accepted architecture
  deviation (same wire behavior). _(MINOR)_
- [ ] **G2-036** — Gateway list rows carry an extra `converted` key. Accepted
  superset (templates use it). _(MINOR)_
- [ ] **G2-037** — `add_query_params` lowercases host / drops port (PHP keeps
  case, drops port+fragment too). Edge-only. _(MINOR)_
- [ ] **G2-044** — Empty-string `due_date` renders `""`; PHP converts it
  (which yields current-date — a PHP quirk). Intentional non-mirror. _(MINOR)_
- [ ] **G2-047** — `index.php:1513` reads `$brandRow` before definition
  (undefined → null path) for default-link currency. Intentionally NOT
  mirrored (falls back to brand currency correctly). _(MINOR)_
- [ ] **G2-050** — `paymentLink` context carries an extra `accept` list per
  file field. Accepted superset. _(MINOR)_
- [ ] **G2-052/053** — `ORDER BY 1` vs `-id`; 404 bodies differ (status
  matches). Accepted. _(MINOR)_

## Area 3 — Money, utils, hooks, DB (`pp-functions.php` 1-800, 3210-3545)

- [ ] **G3-006/008/009** — `generateStrongPassword` uses CSPRNG not
  `str_shuffle`; `getNameChars` keeps `"0"` words + Unicode case; date
  helpers take `strftime` tokens. Same charset/outputs on realistic inputs.
  _(MINOR)_
- [ ] **G3-011/018/020/022** — No cookie helpers, `getAdminPath`,
  MySQL-charset function, or `dateformat` default use (all callers pass
  explicit formats). Dead/low-use surface. _(MINOR)_
  update (PHP `getData`/`insertData`/`updateData` coerce). Code consistently
  checks `in ("--", "", None)`;ADMIN_TEMPLATE_DEFAULT_FILTERS unset spots may
  render `None`/blank instead of `--`. Open display sweep. _(MINOR)_
- [ ] **G3-016** — `limit_checker` dead code (zero callers in PHP).
  Intentionally unported. _(MINOR)_
- [ ] **G3-017** — `pp_site_url` Full/fulldomain/maindomain modes unported
  (only base URL helper exists, unused). _(MINOR)_
- [ ] **G3-019** — `Authorization`/`Bearer` fallback accepted in addition to
  `MHS-PIPRAPAY-API-KEY`. Accepted (superset). _(MINOR)_
- [ ] **G3-023/025/028** — `convertUTCtoUserTZ` returns `str(input)` on bad
  input; `getParam` coerces ints; webhook worker pool is 10 threads.
  Edge-only. _(MINOR)_
- [ ] **G3-032** — Hook registry is process-lifetime (with per-callback
  crash isolation); PHP is per-request. Accepted architecture deviation.
  _(MINOR)_
- [ ] **G3-033** — Pre-registered identity `invoice.total` filter (PHP's is
  commented out). Behavior-identical. _(MINOR)_
- [ ] **G3-034/035/036/037** — No `QueryBuilder`/`DB` wrapper (Django ORM by
  design); `pp_` prefix hardcoded; `varchar(20)` datetimes need an import
  step for legacy DBs. Accepted architecture deviations. _(MINOR)_

## Area 4 — SMS engine, render, storage (`pp-functions.php` 767-3210)

- [ ] **G4-001/002/005** — Unmatched optional SMS captures (`None` vs
  `null`), `mfs` key trim, `set_env` return value. No observable effect.
  _(MINOR)_
- [ ] **G4-006/007/008/009/011/014/015** — Upload message strings, Pillow vs
  Imagick conversion details (alpha flatten, GIF handling, filename case),
  `deleteImage` bool vs 3-message JSON, extract multi-root nesting,
  `rand()` vs `secrets` filenames. Cosmetic. _(MINOR)_
- [ ] **G4-016** — `callback_url` carries `?gateway=&pp_callback&gid`
  (superset of bare `?pp_callback`); plugins treat it opaquely. _(MINOR)_
- [ ] **G4-020/023/024** — `hexToRgba` interior-`#`, receipt section helpers
  and status colors unported (HTML receipt). _(MINOR)_
- [ ] **G4-026/027/028** — Language fallback is `en` (PHP: first key /
  session-first). Equivalent on all shipped packs (en-first). _(MINOR)_
- [ ] **G4-029** — Framework lang keys fill-if-absent instead of overwrite.
  No key collisions exist in practice. _(MINOR)_
- [ ] **G4-030/031/035/036/037/038** — Rate sanitize, extra `converted` key,
  missing-class dead render, stored-vs-computed display fees, float comma
  format. Cosmetic/edge. _(MINOR)_
  Presentational. _(MINOR)_
- [ ] **G4-041/042/043/044/045/047/052/055** — Empty-`<ol>` render, spinner/
  restore/toast details, `verify/` vs same-URL POST, `action-v2` hidden
  inputs, invoice branch location, bare inputs. Accepted wire/markup
  deviations with identical JSON shapes. _(MINOR)_

## Area 5 — Adapter infra + auth (`pp-adapter.php` 1-700)

- [ ] **G5-001/002/005-022** — `E_ALL` display-off default, `PipraPay_INIT`
  guard, native PHP sessions, per-row requirement-table nuances (Fileinfo,
  Mbstring, Tokenizer, allow_url_fopen, always-PASS rows), requirement gate
  for paths, `pp-config.php` branching, addon loader, SDK 403 guard,
  version hash, favicon constants, site-URL quirk. Accepted
  architecture/platform deviations. _(MINOR)_
- [ ] **G5-024/025/026/027** — No custom CSRF-token echo/rotation,
  `pp-token` HMAC app bypass, or `session_token` JSON bodies (HTML + Django
  CSRF + `csrf_exempt` companion endpoints cover the flows). _(MINOR)_
- [ ] **G5-028/034** — Session keys (`pp_admin_id`, `pp_cookie`) instead of
  `pp_admin`/`pp_2fa` cookies. Accepted session architecture. _(MINOR)_
- [ ] **G5-030/036** — PHP sets login/2FA flags even for suspended users
  (bugs). Intentionally NOT mirrored (deny). _(MINOR)_
- [ ] **G5-037** — No brand-currency template globals (unused by Django
  templates). _(MINOR)_
- [ ] **G5-043** — `BrowserLog` row is written after 2FA success, not at
  password stage. Same end state. _(MINOR)_
- [ ] **G5-045/049/057** — Redirects instead of JSON `target`/`session_token`
  bodies; unknown-action `Oops` branch N/A (URL routing). _(MINOR)_
- [ ] **G5-050** — New cookie per login instead of reusing the 2FA cookie
  (with `cycle_key()` fixation protection). Accepted. _(MINOR)_
- [ ] **G5-058** — No `escape_string` (ORM parameterization). _(MINOR)_

## Area 6 — Staff admin A–M (adapter staff→general-setting)

- [ ] **G6-001/003/006/008/015/025/030/034/038/058/064/065/067/073/074/079** —
  Cosmetic: list-scope message text, date filters present (staff), value
  shapes, `deleteable` search parity, domain/gateway info endpoints (HTML
  flow needs none), gateway required-gate (form posts all fields), expiry
  message quirk, currency ordering/display, info endpoints, faq bulk perm
  nuance, api list shape. _(MINOR)_
- [ ] **G6-063** — Link `quantity` parsed to int; PHP stores decimal string
  into an INT column (MySQL coerces identically). Same end state. _(MINOR)_

## Area 7 — Staff admin N–Z + companion (adapter 5600-9671)

  minor. _(MINOR)_
- [ ] **G7-011** — Bulk status transitions are whitelisted
  (approved/awaiting-review/used); PHP writes any verbatim actionID.
  Intentional (safer). _(MINOR)_
- [ ] **G7-013** — Auto-create success message omits the `.$amount` leak.
  Intentional. _(MINOR)_
- [ ] **G7-020** — Theme/upload/slip URLs are `/media/storage/…`, not
  `$site_url.'pp-media/storage/…'`. Accepted (Django `MEDIA_URL`). _(MINOR)_
- [ ] **G7-021/022** — Transaction search keeps `ref` (PHP searches
  `gateway_slug`, now also covered); list is HTML not JSON `net_amount`.
  Accepted. _(MINOR)_
- [ ] **G7-035** — Import staging paths differ (`temp/` unwrap vs
  `import/`+`temp/<sanitized>/`). Same end state. _(MINOR)_
- [ ] **G7-044** — Addon config skips framework keys (`action`,
  `csrf_token`, `addon-id`); PHP persists literally everything. Accepted
  (avoids junk rows). _(MINOR)_
- [ ] **G7-052/053** — Manual/slip hook items carry real sender/trx values;
  PHP sends literal `'--'`. Intentional (more informative). _(MINOR)_
- [ ] **G7-054** — Slip upload passes the file object (PHP passes a string
  through a broken emptiness check). Intentional (functional). _(MINOR)_
- [ ] **G7-058** — Code-101 aborts the batch with one JSON body; PHP echoes
  per-SMS JSON and continues (corrupt multi-JSON). Intentional. _(MINOR)_
- [ ] **G7-059** — Duplicate SMS records Code-103 error row and flags batch
  failure at end; PHP echoes failure mid-loop and continues. _(MINOR)_
- [ ] **G7-064** — No `pp_demo_mode` gates (no demo-mode concept). _(MINOR)_

## Area 8 — API gateways (13 plugins)

- [ ] **G8-002/011** — `tran_id`/invoice prefixes use digit/uuid shapes,
  not PHP `rand()`. Cosmetic ids. _(MINOR)_
- [ ] **G8-003/020/040/057/063/071/072** — Customer/address placeholder
  values differ or are omitted (provider-optional fields). _(MINOR)_
- [ ] **G8-007/009/013/024/049/067** — Provider error/success message text
  and `source_info` label wording nuances (behavioral conditions verified
  identical). _(MINOR)_
- [ ] **G8-010** — bKash auth token in per-ref env, not `$_SESSION`.
  Accepted (stateless-safe). _(MINOR)_
- [ ] **G8-014/015** — Refund base-candidate set/order and AWS-error scan
  scope differ. Edge-only. _(MINOR)_
- [ ] **G8-021/048** — EPS `totalAmount` float-cast; PayPal currency case —
  verified accepted (provider tolerates both). _(MINOR)_
- [ ] **G8-029** — `NAGAD_APP_LOG` flag ignored. _(MINOR)_
- [ ] **G8-038** — OxaPay request uses camelCase keys; PHP uses snake_case
  (which the provider docs do not accept — PHP side appears broken).
  Intentional deviation. _(MINOR)_
- [ ] **G8-041/042** — OxaPay IPN verifies with the real key and checks
  amount+ref (PHP HMAC key is wiped by an overwrite bug, and PHP trusts
  GET-paid alone). Intentional fixes. _(MINOR)_
- [ ] **G8-043/044/045/046** — PathaoPay ref/token/session-vs-env nuances.
  _(MINOR)_
- [ ] **G8-058** — TLS verified on PayStation status call (PHP disables).
  Intentional (safer). _(MINOR)_
- [ ] **G8-076/077** — Stripe requires session `url`, extra PI-less GET.
  Same outcomes. _(MINOR)_

## Area 9 — Automation/manual gateways (35 plugins)

- [ ] **G9-002/003/004/005** — QR SVG label discarded, extra top-level QR
  value, missing-`mobile_number` notice suppression, missing-amount `""`
  default. No rendered effect. _(MINOR)_

## Area 10 — Themes, install, misc

- [ ] **G10-007/008/009/010/011/012/013/014/015/018/019/020/023/026/027** —
  Checkout chrome cosmetics: `[]`-vs-`—` empty lists, favicon/`pp_assets`
  bundles, button CSS, SEO/analytics/bg-image (options ARE read — verify
  wiring per page), logo images, icon bar, gateway logos, hide-empty-tab
  CSS (buttons now conditional), escaping (Django autoescape covers),
  total banner style, watermark (wired), `hitLanguage` direct links.
  _(MINOR)_
- [ ] **G10-034/041** — Receipt is printable HTML, not FPDF. Accepted
  (documented). _(MINOR)_
- [ ] **G10-036/042** — Status SVG icons; canceled-table hide matches.
  _(MINOR)_
- [ ] **G10-050/051/054** — Print buttons and spinner/toast details.
  Partially wired (spinners/toasts on verify forms). _(MINOR)_
- [ ] **G10-058/059/060/061** — Web installer covers requirements/admin/
  complete; no DB-driver picker UI, no password-strength meter, no
  delete-installer reminder beyond text. _(MINOR)_
- [ ] **G10-062/063** — CharFields instead of enums, `auto_now` datetimes,
  surrogate `id`, wider `2fa_secret`. Accepted ORM mapping. _(MINOR)_
- [ ] **G10-067** — No demo prefill / remember-me (dead UI in PHP) / JSON
  rotation. Accepted. _(MINOR)_
- [ ] **G10-073/075/079** — Django CSRF, full-page loads, fixed 6-digit
  TOTP. Accepted. _(MINOR)_
- [ ] **G10-081** — Maintenance via middleware (with system-settings
  exemption). Accepted. _(MINOR)_

## Refuted agent claims (verified against source — NOT gaps)

- **G2-035** — `?lang=` IS handled in PHP (`checkout.php:7-12` via
  `pp_set_lang`); Django matches via session. Not a gap.
- **G2-046/agent contradiction** — `index.php` invoice DISPLAY uses flat
  VAT; theme template + `action-v2` create use percent VAT. Both mirrored
  per surface (`invoice_display_totals` vs `invoice_line_totals`).
- **G4-032** — No min/max enforcement exists on the PHP gateway render
  path (`pp_gateway_render` never checks); listing-only gating mirrored.
- **G4-053** — No `hint` column exists in `pp_payment_link_field`; nothing
  to render.
- **G7-047** — The `&&`/`||` precedence claim was wrong:
  `(select&&v)||(file&&v)||(checkbox&&v)||(radio&&v)` covers all four
  types; Django matches.
- **G2-012 (as stated)** — `is_numeric(" 12 ")` is TRUE in PHP (returns
  `" 12 "`, DB-coerced to 12); Django normalizes to `"12"` — same end
  state, not `"0"`.

## Cleared during this audit pass (deleted from above, kept here as log)

- Router: `?page=` shim, traversal→`/`, install + requirement gates,
  `/ipn/` empty→400, IPN brand/lang/money context, cron limit cast +
  half-up FX + int `http_code` + verbatim `local_net`.
- API: scope-after-body order, loose API domain check, explicit-`"--"`
  rejection, `""`-metadata→400, list-metadata accept, scientific/padded
  amounts, raw-email validation, rounded verify/refund totals, `""`
  customer preservation, refund source only-when-changed, theme
  `<slug>-<field>` keys, unescaped-unicode customer JSON, 403 invalid
  theme, `None` customer id, raw wall-clock dates, `"--"`-only
  logo/name fallbacks, resolved locale, webhook text bodies, refunded
  `gateway_id` write, exact webhook hook shape, flat display VAT,
  link dict keys/defaults/nesting, explicit-null currency.
- Money: leading-prefix sanitize, tiny-divisor `"0"`, `isExpired`
  `"0"` + `d-m-Y`/`m/d/Y`, case-insensitive UA, calendar `time_ago` +
  future→Just now, crash-isolated hooks, 3s/5s `Connection: close`
  unescaped-unicode webhooks, atomic file-or-string `run_sql`,
  restorable SQL backup, MySQL `utf8mb4` options, `num_eq` juggling.
- Env: `""`-on-miss + auto-insert + exact-brand reads (no `both` leak).
- Render/storage: mkdir error dict, no-slash `ipn_url`, vendored Tabler/
  jQuery/QR assets, rounded receipt line, bank fallback lang+slip,
  exact instruction markup + `escapejs`, framework labels, payment-link
  labels/stars/amount-first/`required`/`accept`/`[]`, 14-key gateway
  context, brand+gateway plugin data, gateway `supported_languages`
  switcher, copy toasts, submit spinners, gateway colors CSS.
- Auth: bcrypt hasher, RFC email detection, exact messages, forgot
  coercion, POST-only set-brand + brand check, `BrowserLog` session gate,
  brand-row gate, `cycle_key()`, current-password/TOTP gates (profile +
  session logout), 2FA recheck + incomplete-boxes message, six-box UI,
  `email-address` forgot field, password toggle, `?logout` middleware,
  QR image, maintenance/404 copy.
- Staff: brand scoping + switcher, cascade deletes + guards + bulk,
  validation everywhere, matrix permissions, per-id grant edit,
  `delete_permission` key semantics, fan-out seeding, domain
  normalization, 16-hex cron key, bucketed dashboard series, FX reports
  + trends + range validation, multi-customer invoices + hooks + item
  upserts, link expiry display + bulk + cascades + validation,
  gist currency import, sync-all + errors, dedicated `geneal` handler,
  required FAQ, view-scoped API keys + raw scopes + bulk, full brand
  form + logo handling, used-only devices + connected filter + search +
  cascades, strict balance rules + `iupdate` + info JSON + dup checks,
  SMS tabs + exclusion + search + info JSON, current-brand theme
  activate + slug-prefixed undeclared keys, transaction slug search +
  bulk IPN log + hooks, checksum-scan update check, gated verified
  download, strict hash + versioned backups + temp-dir install,
  `zip_file` import key, registry-validated gateway create + bank force
  + undeclared params + `--` empties, addon slug register + cascades,
  permission pre-check UI, deleteable flags, domain filters, base-first
  currencies, reports/dashboard templates.
- Gateways: sandbox-default bases, `&`-joined Stripe return, Binance
  string statuses + persisted expected amount, cancel-stays-initiated,
  Nagad status gate + Dhaka clock + client IP + digit invoice + bare
  callback, EPS order/phone/`&&` logic + messages, PayPal half-up
  amount + uppercase + description + capture-only-Prefer + capture
  refresh + nested paths + labels + case-insensitive rel, PayStation
  nested `data.*`, ShurjoPay prefix/store/postcode/JSON-verify/cancel
  ref + label, SSLCommerz `emi_option` + product keys + verified
  `tran_id` + labels, NOWPayments full coin list + invoice-id guards +
  labels + 500-secret, OxaPay juggling compare, extractor `{currency}`
  fix + regeneration, `icon.png` logo, bKash error-code composition +
  `refundTransactions` scan.
- Cron: verified `local_net`, explicit stamps, int codes.
- Tests: 88 parity tests green; `?page=`, installer, pairing, webhook,
  cascade, gateway-condition coverage added.

- Continuation pass: `pp`/`pp_blank` display filters (`core/templatetags/`)
  normalize NULL/`--` cells (G3-013/014/015, G7-010); `get_param` requires
  strings (G3-025); `set_env` returns the value (G4-005); QR action renders
  the Tabler QR SVG + blurred modal (G4-040, G10-033); brand rows carry
  deleteable flags with disabled checkboxes (G6-030); IPN language resolves
  against gateway `supported_languages` (G1-022); IPN reads `request.body`
  before POST (fixes form-POST IPNs like Binance).
