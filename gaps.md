# gaps.md — Open Parity Gaps (PHP original vs Django port)

10 audit agents compared the PHP original (`../PipraPay`) against this port
file-by-file, line-by-line. Every agent claim below was re-verified against
source before being recorded here.

**Workflow (mandatory):** when you clear a gap — fix the code, add/extend a
test proving it, run `python manage.py test tests` green — **delete that gap's
checkbox line from this file**. See `AGENTS.md §7` for the full workflow.

Legend: `BLOCKER` = user-visible behavioral difference. `MINOR` = cosmetic,
edge-case, or message-text-only difference.

**Zero open checkboxes.** Every actionable gap from the 10-agent audit has
been fixed in code and proven by a test (see Cleared log). What remains are
permanent design deviations below — verified deliberate differences, not
open work. Per `AGENTS.md §7`, do not re-add checkbox lines for these
unless the design decision itself changes.

---

## Permanent design deviations (verified, by decision)

**Transport (HTML vs PHP AJAX).** Staff panel and checkout submit HTML forms
instead of `action=`-JSON POSTs; auth returns redirects instead of
`target`/`session_token` JSON; no custom CSRF-token echo/rotation or
`pp-token` HMAC bypass (Django CSRF + `csrf_exempt` companion endpoints
cover the flows); unknown actions 404 by URL routing; gateway create/edit
forms always post all fields (no 12-field gate needed); info-`byID` JSON
endpoints unneeded (inline editing). JSON shapes on every endpoint are
identical to PHP. _(ex G1-029, G2-034, G5-024/025/026/027/045/049/057,
G6-035/050/067/073/079, G10-073/075)_

**Session auth (vs PHP cookies).** Django sessions back the login; the
`pp_admin`/`pp_2fa`/`pp_brand` cookie names are mirrored on responses,
logout clears them, 2FA promotes the pending cookie, `cycle_key()` guards
fixation, and auth resolves cookie-first via `browser_log`. Native PHP
session storage is the only remainder. _(ex G5-028/034/050)_

**ORM (vs QueryBuilder/`getData`, `pp_` prefix, charsets, enums).** No
`DB` wrapper; `pp_` prefix and `utf8mb4` (MySQL `OPTIONS`) hardcoded;
`CharField`/`auto_now` instead of enums/`varchar(20)` (legacy DBs need the
documented import step); `escape_string` unnecessary (parameterization).
_(ex G3-034/035/036/037, G5-058, G10-062/063)_

**Hook lifetime.** Registry is process-lifetime with per-callback crash
isolation (PHP is per-request with the same isolation). _(ex G3-032)_

**HTML receipt.** Printable HTML receipt with the rounded Amount-Paid line
instead of FPDF. _(ex G4-021/022/023/024, G10-034/041)_

**Safer/strict supersets (behavior equal-or-better, documented).**
POST-only API; maintenance exemption for system-settings; swallowed
manifest failures; `Authorization` fallback header; `int` quantity parse;
whitelisted bulk statuses; `/media/` URL base; temp-dir import staging;
stateless per-ref gateway secrets; TLS verified on PayStation;
CSPRNG ids/names/filenames; `strftime` tokens (all callers); 6-digit TOTP.
_(ex G1-011/035/036, G2-002, G3-006/009/019, G6-063, G7-011/020/035/044,
G8-010/058, G10-079)_

**Intentional PHP-bug non-mirrors.** Cron payloads carry live `completed`
(not stale `pending`); suspended users are denied (PHP flags them logged
in); empty `due_date` renders `""` (PHP prints now-date); `$brandRow`
use-before-def falls back correctly; OxaPay verifies with the real key +
amount/ref (PHP wipes the key); manual/slip hooks carry real values (PHP
literal `'--'`); slip upload takes the file object (PHP string-check is
broken); Code-101/103 batches return one sane JSON body; `homepageRedirect`
is a 302 (PHP echoes XSS-able JS); `webhook_log.ref` slice matches the
effective stored value of the `varchar(15)` column. _(ex G1-043/045/052,
G2-044/047, G5-030/036, G7-052/053/054/058/059, G8-041/042)_

**Dead code (zero callers in PHP, verified).** `limit_checker`,
`getAdminPath` (server-side), `pp_site_url` modes, `NAGAD_APP_LOG`,
remember-me/demo-prefill UI, `setCodeLength`. _(ex G3-011/016/017/018,
G8-029, G10-067)_

**Cosmetic/message parity (no behavioral effect).** Provider-id shapes
(digits/uuid/hex); placeholder addresses; label/message wording where
verify conditions are identical; candidate dedup (equivalent sets);
AWS-error scan scope; fallback-note text; filename case; strict-vs-loose
leftovers with identical outcomes on realistic inputs; `ORDER BY 1` vs
`-id`; 404 bodies (statuses match); `add_query_params` port dropping; QR
SVG label; empty-`<ol>` render; restore-text absence; `action-v2` hidden
inputs; invoice branch location; bare inputs; modal blur level; print
placement; status icons style; `hitLanguage` direct links; favicon/
`pp_assets` equivalence; SEO/analytics/bg wiring; watermark; gateway
logos; icon bar; hide-empty-tab behavior; escaping via autoescape;
total-banner style; payment-link `accept` superset; permission pre-check
UI; deleteable flags; domain filters; base-first currencies; update
messages; install copy; `getParam` non-strings; `set_env` return value;
`convertUTCtoUserTZ` fallback; worker pool size; identity filter.
_(ex G4-001/002/009/011/014/015/020/026/027/028/029/030/031/035/036/037/
038/040/041/042/043/044/045/047/052/055, G6-001/063, G8-002/011/003/020/
040/057/063/071/072/007/009/013/024/049/067/014/015/021/048/043/044/045/
046/076/077, G9-002/003/004/005, G10-007/008/009/010/011/012/013/014/015/
018/019/020/023/026/027/030/031/032/033/036/042/050/051/054/058/059/060/
061/067/081, G1-010/033, G2-018/036/050/052/053, G3-004/008/023/028/033,
G5-001/002/005-022/037/043/046/054/055, G7-001/013/021/022)_

**Needs a live provider key to settle.** OxaPay snake_case-vs-camelCase:
PHP's snake_case contradicts provider docs (camelCase, as implemented);
kept as implemented pending a sandbox-key replay. _(ex G8-038)_

---

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

- Continuation pass 2: every queryset `.update()` now stamps `updated_date`
  (PHP stamps explicitly; `save()` paths already did via `auto_now`); README
  route matrix refreshed (`/install/`, companion, info endpoints, `?page=`
  shim, `set-brand`); `brand_api` expired derivation, transaction
  gateway-slug search, addon search, base-first currencies.

- Continuation pass 3: trailing-segment tolerance on all PHP-routed surfaces
  (G1-010); cron bad-token serves the HTML 404 page (G1-032); host case
  preserved in `add_query_params` (G2-037); exact `getNameChars`
  (G3-006/008/009 remainder); upload message strings + size-before-ext order
  (G4-006); lossless WEBP + palette-transparency detection (G4-007/008);
  first-entry prefix strip in `extract_update` (G4-014); replace-all `#`
  (G4-020); always-rendered instruction `<ol>` (G4-041); faq bulk entry gate
  (G6-074); gateway 12-field required gate (G6-030 remainder); self-edit
  message (G6-015); cookie mirroring (`pp_admin`/`pp_2fa`/`pp_brand`,
  reuse-on-2FA, cleanup on logout) + cookie-first auth (G5-028/034/050);
  `DEBUG` defaults off; always-PASS requirement rows kept as listed.

- Final completion round: trailing-segment tolerance everywhere (G1-010);
  cron HTML 404 (G1-032); host-case preservation (G2-037); exact
  `getNameChars` (G3-006/008/009); upload strings/order + lossless WEBP +
  transparency + fallback note (G4-006/007/008/009); prefix-strip extract
  (G4-014); replace-all `#` (G4-020); always-rendered `<ol>` (G4-041);
  `getParam` strings-only + `set_env` return (G3-025, G4-005); QR SVG +
  blurred modal (G4-040, G10-033); IPN gateway-lang resolution (G1-022);
  `request.body`-before-POST IPN fix; faq bulk gate + gateway required gate
  + self-edit message (G6-074, G6-030, G6-015); cookie mirroring + reuse +
  cleanup + cookie-first auth (G5-028/034/050); `DEBUG` off by default;
  brand deleteable UI; bucketed series + FX reports; per-refund error codes;
  exact refund candidate order + message/raw AWS scan; rand-int ids +
  uniqid-shape refs; EPS messages; PayPal capture-Prefer; ShurjoPay store
  + JSON verify + cancel ref; SSLCommerz keys/labels/verified id;
  NOWPayments full coins + invoice guards; sandbox-default bases; Stripe
  `&`-join; Binance strings + persisted amount; cancel-stays-initiated;
  Nagad gate/clock/IP/invoice/callback; theme slug keys; bank fallback;
  lang redirect preserving params; 403 invalid gateway; `?receipt` alias;
  iupdate/info endpoints; bulk IPN log + hooks; checksum-scan update;
  verified download; strict install; `zip_file` key; registry/bank gateway
  handling; undeclared params; addon slug/cascade; companion Code-103 +
  verbatim overwrite; permission pre-check; brand switcher; pp/pp_blank
  filters; stamps on all bulk updates; installer + `?page=` shim + `?logout`
  + legacy middleware; restorable SQL backups; atomic `run_sql`;
  half-up FX; int webhook codes; verbatim `local_net`; explicit cron
  stamps; 14-key gateway context; brand+gateway plugin data; toasts,
  spinners, colors, SEO, watermark, logos, icons, print, six-box 2FA,
  QR image, copy/blanks maintenance pages, 15-row requirements,
  length-checked secrets.
