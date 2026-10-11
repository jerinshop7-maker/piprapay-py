"""
Staff admin panel views — port of pp-content/pp-admin/pp-root/*.

Pages: dashboard, activities, customers, transaction(+edit), invoice CRUD,
payment-link CRUD, gateways(+create-bank), addons, brand-setting/*
(api/currency/faq/general/themes), brands CRUD, devices(+balance-verification),
domains, sms-data, staff-management CRUD(+permissions), reports,
my-account, system-settings(cron/general/import/update).
"""
import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from core import money as M
from core.env import get_env, set_env
from core.models import (Admin, ApiKey, BalanceVerification, Brand, BrowserLog, Currency,
                         Customer, Device, Domain, Faq, Gateway, GatewayParameter, Invoice,
                         InvoiceItem, PaymentLink, Permission, SmsData, Transaction, WebhookLog)
from core.permissions import can_access_page, has_permission, permission_schema
from core.totp import generate_secret, otpauth_url, verify_totp
from core.webhooks import send_ipn_multi
from gateways.registry import all_gateways
from staff.auth import login_required, page_required, perm_required


def _paginate(request, qs, per_page=20):
    return Paginator(qs, per_page).get_page(request.GET.get("page", 1))


def _scoped(qs, request, field: str = "brand_id"):
    """Brand-scope a queryset to the session brand (PHP $global_response_brand)."""
    brand_id = getattr(request, "pp_brand_id", "")
    if brand_id:
        qs = qs.filter(**{field: brand_id})
    return qs


def _post_brand(request, default: str = "") -> str:
    return request.POST.get("brand_id", "") or getattr(request, "pp_brand_id", "") or default


@login_required
def index(request):
    return redirect("/admin/dashboard/")


@login_required
def dashboard(request):
    brand_id = request.GET.get("brand") or ""
    txns = Transaction.objects.all()
    if brand_id:
        txns = txns.filter(brand_id=brand_id)
    now = timezone.now()
    last30 = now - timedelta(days=30)
    completed = txns.filter(status="completed", created_date__gte=last30)
    volume = completed.aggregate(s=Sum("amount"))["s"] or 0
    # PHP Total Payments card excludes `initiated`.
    visible = txns.exclude(status="initiated")
    ctx = {
        "total_transactions": visible.count(),
        "completed": txns.filter(status="completed").count(),
        "pending": txns.filter(status="pending").count(),
        "volume_30d": M.money_round(str(volume)),
        "recent": txns.order_by("-id")[:10],
        "brands": Brand.objects.all(),
        "brand_id": brand_id,
        "last_cron": M.time_ago(get_env("last-cron-invocation"), "Asia/Dhaka"),
    }
    return render(request, "staff/dashboard.html", ctx)


@login_required
def dashboard_transaction_stats(request):
    """Mirror dashboard-transaction-statistics: per-bucket labels/total/
    complete/pending series (today hourly, week daily, month daily, year
    monthly, custom daily), brand-scoped."""
    from django.http import JsonResponse
    preset, start, end = _resolve_range(request)
    qs = Transaction.objects.exclude(status="initiated")
    brand_id = request.GET.get("brand") or getattr(request, "pp_brand_id", "")
    if brand_id:
        qs = qs.filter(brand_id=brand_id)
    if start:
        qs = qs.filter(created_date__gte=start)
    if end:
        qs = qs.filter(created_date__lte=end)
    from datetime import datetime, timedelta
    buckets: list[tuple[str, str, str]] = []
    today = datetime.now().date()
    if preset == "today":
        for h in range(24):
            buckets.append((f"{h:02d}:00", f"{today} {h:02d}:00:00", f"{today} {h:02d}:59:59"))
    elif preset in ("this_week", "last_week"):
        base = datetime.strptime(start[:10], "%Y-%m-%d").date() if start else today
        for i in range(7):
            d = base + timedelta(days=i)
            buckets.append((d.strftime("%a"), f"{d} 00:00:00", f"{d} 23:59:59"))
    elif preset in ("this_year", "previous_year"):
        year = int((start[:4] if start else today.year))
        for m in range(1, 13):
            buckets.append((datetime(year, m, 1).strftime("%b"),
                            f"{year}-{m:02d}-01 00:00:00", f"{year}-{m:02d}-31 23:59:59"))
    else:
        try:
            s = datetime.strptime((start[:10] if start else str(today)), "%Y-%m-%d").date()
            e = datetime.strptime((end[:10] if end else str(today)), "%Y-%m-%d").date()
        except ValueError:
            s = e = today
        d = s
        while d <= e and len(buckets) < 366:
            buckets.append((d.strftime("%d %b"), f"{d} 00:00:00", f"{d} 23:59:59"))
            d += timedelta(days=1)
    series = []
    for label, s_from, s_until in buckets:
        bq = qs.filter(created_date__gte=s_from, created_date__lte=s_until)
        series.append({"label": label, "total": bq.count(),
                       "completed": bq.filter(status="completed").count(),
                       "pending": bq.filter(status="pending").count()})
    return JsonResponse({
        "total": qs.count(),
        "completed": qs.filter(status="completed").count(),
        "pending": qs.filter(status="pending").count(),
        "volume": M.money_round(str(qs.filter(status="completed").aggregate(s=Sum("amount"))["s"] or 0)),
        "labels": [b["label"] for b in series],
        "series": series,
    })


@login_required
def dashboard_gateway_stats(request):
    """Mirror dashboard-gateway-statistics: per-gateway counts with display
    names, completed-only, brand-scoped, No-Data fallback."""
    from django.http import JsonResponse
    qs = Transaction.objects.exclude(status="initiated")
    brand_id = request.GET.get("brand") or getattr(request, "pp_brand_id", "")
    if brand_id:
        qs = qs.filter(brand_id=brand_id)
    rows = []
    for entry in qs.values("gateway_id").annotate(n=Count("id")).order_by("-n"):
        gw = Gateway.objects.filter(gateway_id=entry["gateway_id"]).first()
        rows.append({"gateway_id": entry["gateway_id"],
                     "name": gw.display if gw and gw.display else entry["gateway_id"],
                     "n": entry["n"]})
    if not rows:
        rows = [{"gateway_id": "--", "name": "No Data", "n": 0}]
    return JsonResponse({"gateways": rows})


@login_required
@page_required("transaction")
def transaction_list(request):
    qs = _scoped(Transaction.objects.exclude(status="initiated").order_by("-id"), request)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_until = request.GET.get("until", "").strip()
    if q:
        slug_ids = Gateway.objects.filter(slug__icontains=q).values_list("gateway_id", flat=True)
        qs = qs.filter(Q(ref__icontains=q) | Q(trx_id__icontains=q) | Q(sender__icontains=q)
                       | Q(customer_info__icontains=q) | Q(gateway_id__in=list(slug_ids)))
    if status:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(created_date__gte=f"{date_from} 00:00:00")
    if date_until:
        qs = qs.filter(created_date__lte=f"{date_until} 23:59:59")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "transaction", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Transaction.objects.filter(id=request.POST.get("delete")).delete()
            messages.success(request, "Transaction deleted.")
            return redirect("/admin/transaction/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            need = {"deleted": "delete", "approved": "approve", "canceled": "cancel",
                    "refunded": "refund", "ipnsend": "send_ipn"}.get(action, "")
            if not need or not has_permission(request.pp_permissions, "transaction", need,
                                              request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            hook_rows = []
            for txn in Transaction.objects.filter(id__in=ids):
                if action == "deleted":
                    txn.delete()
                elif action == "approved":
                    txn.status = "completed"
                    txn.save()
                    hook_rows.append(txn)
                elif action == "canceled":
                    txn.status = "canceled"
                    txn.save()
                    hook_rows.append(txn)
                elif action == "refunded":
                    _refund_transaction(txn)
                    hook_rows.append(txn)
                elif action == "ipnsend":
                    code = _send_transaction_ipn(txn)
                    if code != 200 and txn.webhook_url and txn.webhook_url != "--":
                        import random as _r
                        from core.transactions import transaction_payload as _payload
                        WebhookLog.objects.create(
                            ref=_r.randint(1, 2 ** 31 - 1), brand_id=txn.brand_id,
                            payload=json.dumps(_payload(txn), ensure_ascii=False), url=txn.webhook_url)
            from core import hooks as _hooks
            if hook_rows:
                from core.transactions import transaction_payload as _payload2
                _hooks.do_action("transactions.updated", [_payload2(t) for t in hook_rows])
            messages.success(request, "Bulk action completed.")
            return redirect("/admin/transaction/")
    _tq = _scoped(Transaction.objects.exclude(status="initiated"), request)
    tabs = {s: _tq.filter(status=s).count()
            for s in ("completed", "pending", "refunded", "canceled")}
    return render(request, "staff/transaction_list.html",
                  {"page": _paginate(request, qs), "q": q, "status": status,
                   "date_from": date_from, "date_until": date_until, "tabs": tabs})


def _refund_transaction(txn: Transaction) -> None:
    """Mirror transaction-bulk-action refund: completed-only + live bKash refund."""
    if txn.status != "completed":
        return
    gw = Gateway.objects.filter(gateway_id=txn.gateway_id, brand_id=txn.brand_id).first()
    if gw and gw.slug == "bkash-api-tokenized":
        from gateways.bkash_refund import bkash_tokenized_refund
        result = bkash_tokenized_refund(txn, {"amount": M.money_round(str(txn.local_net_amount)),
                                              "sku": txn.ref, "reason": "Admin refund"})
        if not result.get("status"):
            return
        try:
            info = json.loads(txn.source_info or "[]")
            if not isinstance(info, list):
                info = []
        except ValueError:
            info = []
        changed = False
        for label, key in (("Refund TrxID", "refundTrxId"), ("Refund Amount", "refundAmount"),
                           ("Refund Time", "completedTime")):
            val = (result.get("data") or {}).get(key, "")
            if val:
                info.append({"label": label, "value": val})
                changed = True
        if changed:
            txn.source_info = json.dumps(info, ensure_ascii=False)
    txn.status = "refunded"
    txn.save()


def _send_transaction_ipn(txn: Transaction) -> int:
    """Mirror transaction-ipn: its own payload shape (gateway `name`, rounded
    total, no local_currency) via single sendIPN; skipped when webhook is '--'.

    Deviation: customer names come from customer_info (PHP reads nonexistent
    top-level txn keys, so it always sends 'N/A')."""
    if not txn.webhook_url or txn.webhook_url == "--":
        return 0
    from core.transactions import transaction_payload
    from core.webhooks import send_ipn
    base = transaction_payload(txn, name_field="name")
    payload = {k: base[k] for k in ("pp_id", "full_name", "email_address", "mobile_number",
                                    "gateway", "amount", "fee", "discount_amount",
                                    "local_net_amount", "currency", "metadata", "sender",
                                    "transaction_id", "status", "date")}
    payload["total"] = M.money_round(base["total"])
    try:
        import requests
        r = requests.post(txn.webhook_url, json=payload, timeout=15)
        return r.status_code
    except Exception:
        return send_ipn(txn.webhook_url, payload)


@login_required
@page_required("transaction")
def transaction_edit(request, pk: int):
    txn = Transaction.objects.filter(id=pk).exclude(status="initiated").first()
    if not txn:
        return render(request, "core/404.html", status=404)
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "transaction", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            txn.delete()
            messages.success(request, "Transaction deleted.")
            return redirect("/admin/transaction/")
        action = request.POST.get("action")
        if action in ("approve", "cancel", "refund", "send_ipn"):
            need = {"approve": "approve", "cancel": "cancel", "refund": "refund", "send_ipn": "send_ipn"}[action]
            if not has_permission(request.pp_permissions, "transaction", need, request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            if action == "approve":
                txn.status = "completed"
                txn.sender = request.POST.get("sender", txn.sender)
                txn.trx_id = request.POST.get("trx_id", txn.trx_id)
                txn.save()
            elif action == "cancel":
                txn.status = "canceled"
                txn.save()
            elif action == "refund":
                _refund_transaction(txn)
            elif action == "send_ipn":
                code = _send_transaction_ipn(txn)
                messages.info(request, f"Webhook delivery HTTP {code}.")
                return redirect(f"/admin/transaction/{pk}/")
            messages.success(request, f"Transaction {action}d.")
            return redirect("/admin/transaction/")
    try:
        metadata = json.loads(txn.metadata or "{}")
    except ValueError:
        metadata = {}
    try:
        source_info = json.loads(txn.source_info or "[]")
        if not isinstance(source_info, list):
            source_info = []
    except ValueError:
        source_info = []
    try:
        customer = json.loads(txn.customer_info or "{}")
    except ValueError:
        customer = {}
    return render(request, "staff/transaction_edit.html",
                  {"txn": txn, "metadata": metadata, "source_info": source_info, "customer": customer})


@login_required
@page_required("customers")
def customers(request):
    qs = _scoped(Customer.objects.all().order_by("-id"), request)
    q = request.GET.get("q", "").strip()
    tab = request.GET.get("tab", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_until = request.GET.get("until", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(mobile__icontains=q))
    if tab in ("manual", "checkout"):
        qs = qs.filter(inserted_via=tab)
    if status in ("active", "suspend"):
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(created_date__gte=f"{date_from} 00:00:00")
    if date_until:
        qs = qs.filter(created_date__lte=f"{date_until} 23:59:59")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "customers", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Customer.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/customers/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "customers", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Customer.objects.filter(id__in=ids).delete()
            elif action in ("activated", "suspended"):
                if not has_permission(request.pp_permissions, "customers", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Customer.objects.filter(id__in=ids).update(
                    status="active" if action == "activated" else "suspend", updated_date=timezone.now())
            return redirect("/admin/customers/")
        if "suspend" in request.POST:
            if not has_permission(request.pp_permissions, "customers", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            c = Customer.objects.filter(id=request.POST["suspend"]).first()
            if c:
                c.status = "suspend" if c.status == "active" else "active"
                c.suspend_reason = request.POST.get("reason", c.suspend_reason or "")
                c.save()
                return redirect("/admin/customers/")
        if "edit_id" in request.POST:
            if not has_permission(request.pp_permissions, "customers", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            c = Customer.objects.filter(id=request.POST["edit_id"]).first()
            if c:
                _email = request.POST.get("email", c.email)
                _status = request.POST.get("status", c.status)
                if _status not in ("active", "suspend"):
                    messages.error(request, "Please fill in all required fields before proceeding.")
                    return redirect("/admin/customers/")
                try:
                    from django.core.exceptions import ValidationError as _VE2
                    from django.core.validators import validate_email as _ve2
                    _ve2(_email)
                except _VE2:
                    messages.error(request, "Please enter a valid email address.")
                    return redirect("/admin/customers/")
                if Customer.objects.filter(brand_id=c.brand_id, email=_email).exclude(id=c.id).exists():
                    messages.error(request, "Customer email already exists for this brand.")
                    return redirect("/admin/customers/")
                c.name = request.POST.get("name", c.name)
                c.email = _email
                c.mobile = request.POST.get("mobile", c.mobile)
                c.status = _status
                c.suspend_reason = request.POST.get("suspend_reason", "") or "--"
                c.save()
            return redirect("/admin/customers/")
        # create
        if not has_permission(request.pp_permissions, "customers", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        _name, _email, _mobile = request.POST.get("name", ""), request.POST.get("email", ""), request.POST.get("mobile", "")
        _status = request.POST.get("status", "active")
        if not _name or not _email or not _mobile or not _status:
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/customers/")
        if _status not in ("active", "suspend"):
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/customers/")
        try:
            from django.core.exceptions import ValidationError as _VE
            from django.core.validators import validate_email as _ve
            _ve(_email)
        except _VE:
            messages.error(request, "Please enter a valid email address.")
            return redirect("/admin/customers/")
        if Customer.objects.filter(brand_id=_post_brand(request), email=_email).exists():
            messages.error(request, "Customer email already exists for this brand.")
            return redirect("/admin/customers/")
        Customer.objects.create(ref=M.generate_item_id(), brand_id=_post_brand(request),
                                name=request.POST.get("name", ""), email=request.POST.get("email", ""),
                                mobile=request.POST.get("mobile", ""),
                                status=request.POST.get("status", "active"),
                                suspend_reason=request.POST.get("suspend_reason", ""))
        return redirect("/admin/customers/")
    return render(request, "staff/customers.html",
                  {"page": _paginate(request, qs), "q": q, "tab": tab, "status": status,
                   "date_from": date_from, "date_until": date_until, "brands": Brand.objects.all()})


@login_required
@page_required("invoice")
def invoice_list(request):
    qs = _scoped(Invoice.objects.all().order_by("-id"), request)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(ref__icontains=q) | Q(customer_info__icontains=q))
    if status in ("paid", "unpaid", "refunded", "canceled"):
        qs = qs.filter(status=status)
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "invoice", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            ref = request.POST["delete"]
            InvoiceItem.objects.filter(invoice_id=ref).delete()
            Invoice.objects.filter(ref=ref).delete()
            return redirect("/admin/invoice/")
        if "manage_status" in request.POST:
            if not has_permission(request.pp_permissions, "invoice", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            inv = Invoice.objects.filter(ref=request.POST["manage_status"]).first()
            if inv and request.POST.get("status") in ("paid", "unpaid", "refunded", "canceled"):
                inv.status = request.POST["status"]
                inv.save()
                from core import hooks as _hooks
                _hooks.do_action("invoices.updated.status", {"ref": inv.ref, "status": inv.status})
            return redirect("/admin/invoice/")
        if "bulk" in request.POST and request.POST.get("bulk") == "deleted":
            if not has_permission(request.pp_permissions, "invoice", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            refs = request.POST.getlist("selected_ids")
            InvoiceItem.objects.filter(invoice_id__in=refs).delete()
            Invoice.objects.filter(ref__in=refs).delete()
            return redirect("/admin/invoice/")
    tabs = {s: Invoice.objects.filter(status=s).count()
            for s in ("paid", "unpaid", "refunded", "canceled")}
    return render(request, "staff/invoice_list.html",
                  {"page": _paginate(request, qs), "q": q, "status": status, "tabs": tabs})


@login_required
@page_required("invoice")
def invoice_edit(request, ref: str = ""):
    from core import hooks as _hooks
    inv = Invoice.objects.filter(ref=ref).first() if ref else None
    items = list(InvoiceItem.objects.filter(invoice_id=ref)) if ref else []
    if request.method == "POST":
        need = "edit" if ref else "create"
        if not has_permission(request.pp_permissions, "invoice", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        due_date = request.POST.get("due_date", "") or "--"
        if due_date not in ("--", "") and not M.dateformat(due_date, "%Y-%m-%d"):
            messages.error(request, "Due date must be YYYY-MM-DD.")
            return redirect("/admin/invoice/")
        brand_id = request.POST.get("brand_id", "") or getattr(request, "pp_brand_id", "")

        def _item_rows():
            rows = []
            descs = request.POST.getlist("item_description")
            ids = request.POST.getlist("item_id")
            for i, d in enumerate(descs):
                def _g(name, default="0"):
                    vals = request.POST.getlist(name)
                    return vals[i] if i < len(vals) else default
                try:
                    qty = int(float(_g("item_quantity", "1") or 1))
                except (ValueError, TypeError):
                    qty = 1
                rows.append({"id": ids[i] if i < len(ids) else "",
                             "description": d, "amount": M.money_sanitize(_g("item_amount")),
                             "quantity": qty, "discount": M.money_sanitize(_g("item_discount")),
                             "vat": M.money_sanitize(_g("item_vat"))})
            return rows

        if inv:
            for k, v in {
                "brand_id": brand_id or inv.brand_id,
                "customer_info": request.POST.get("customer_info", "") or json.dumps({
                    "name": request.POST.get("customer_name", ""),
                    "email": request.POST.get("customer_email", ""),
                    "mobile": request.POST.get("customer_mobile", "")}),
                "gateway_id": request.POST.get("gateway_id", inv.gateway_id),
                "currency": request.POST.get("currency", inv.currency),
                "due_date": due_date, "shipping": M.money_sanitize(request.POST.get("shipping", "0")),
                "status": request.POST.get("status", inv.status),
                "note": request.POST.get("note", inv.note or ""),
                "private_note": request.POST.get("private_note", inv.private_note or ""),
            }.items():
                setattr(inv, k, v)
            inv.save()
            # deleted_items by row id; existing rows updated by item_id; rest inserted.
            for did in request.POST.getlist("deleted_items"):
                if str(did).isdigit():
                    InvoiceItem.objects.filter(id=did, invoice_id=inv.ref).delete()
            for row in _item_rows():
                if row["id"] and str(row["id"]).isdigit():
                    InvoiceItem.objects.filter(id=row["id"], invoice_id=inv.ref).update(
                        description=row["description"], amount=row["amount"],
                        quantity=row["quantity"], discount=row["discount"], vat=row["vat"], updated_date=timezone.now())
                elif row["description"] or row["amount"] not in ("0", ""):
                    InvoiceItem.objects.create(brand_id=inv.brand_id, invoice_id=inv.ref,
                                               description=row["description"], amount=row["amount"],
                                               quantity=row["quantity"], discount=row["discount"],
                                               vat=row["vat"])
            _hooks.do_action("invoices.updated", {"ref": inv.ref, "status": inv.status})
        else:
            # invoice-create: one 27-digit invoice per customer in customers[].
            customer_refs = [c for c in request.POST.getlist("customers") if c]
            if not customer_refs:
                single = request.POST.get("customer_info", "") or json.dumps({
                    "name": request.POST.get("customer_name", ""),
                    "email": request.POST.get("customer_email", ""),
                    "mobile": request.POST.get("customer_mobile", "")})
                customer_refs = [single]
            rows = _item_rows()
            if not rows:
                messages.error(request, "Add Item Required.")
                return redirect("/admin/invoice/create/")
            made = 0
            for cref in customer_refs:
                if Customer.objects.filter(email=cref).exists():
                    c = Customer.objects.filter(email=cref).first()
                    customer_info = json.dumps({"name": c.name, "email": c.email, "mobile": c.mobile})
                else:
                    try:
                        json.loads(cref)
                        customer_info = cref
                    except ValueError:
                        customer_info = json.dumps({"name": cref, "email": "", "mobile": ""})
                new_ref = M.generate_item_id(27, 27)
                new_inv = Invoice.objects.create(
                    ref=new_ref, brand_id=brand_id,
                    customer_info=customer_info or "{}",
                    currency=request.POST.get("currency", "BDT"), due_date=due_date,
                    shipping=M.money_sanitize(request.POST.get("shipping", "0")),
                    status=request.POST.get("status", "unpaid"),
                    note=request.POST.get("note", ""), private_note=request.POST.get("private_note", ""))
                for row in rows:
                    InvoiceItem.objects.create(brand_id=brand_id, invoice_id=new_ref,
                                               description=row["description"], amount=row["amount"],
                                               quantity=row["quantity"], discount=row["discount"],
                                               vat=row["vat"])
                _hooks.do_action("invoices.created", {"ref": new_ref})
                made += 1
                inv = new_inv
            if made > 1:
                messages.success(request, f"{made} invoices created.")
                return redirect("/admin/invoice/")
        messages.success(request, "Invoice saved.")
        return redirect("/admin/invoice/")
    try:
        customer = json.loads(inv.customer_info) if inv and inv.customer_info else {}
    except ValueError:
        customer = {}
    return render(request, "staff/invoice_edit.html",
                  {"inv": inv, "items": items, "brands": Brand.objects.all(),
                   "gateways": Gateway.objects.all(), "customer": customer,
                   "customers": Customer.objects.filter(status="active").order_by("-id")[:200]})


@login_required
@page_required("payment_link")
def payment_link_list(request):
    from core.models import PaymentLinkField
    qs = _scoped(PaymentLink.objects.all().order_by("-id"), request)
    status = request.GET.get("status", "").strip()
    if status in ("active", "inactive"):
        qs = qs.filter(status=status)
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "payment_link", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            PaymentLinkField.objects.filter(paymentLinkID=request.POST["delete"]).delete()
            PaymentLink.objects.filter(ref=request.POST["delete"]).delete()
            return redirect("/admin/payment-link/")
        if "manage_status" in request.POST:
            if not has_permission(request.pp_permissions, "payment_link", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            link = PaymentLink.objects.filter(ref=request.POST["manage_status"]).first()
            if link and request.POST.get("status") in ("active", "inactive"):
                link.status = request.POST["status"]
                link.save()
            return redirect("/admin/payment-link/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "payment_link", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                PaymentLinkField.objects.filter(paymentLinkID__in=ids).delete()
                PaymentLink.objects.filter(ref__in=ids).delete()
            elif action in ("activated", "inactivated"):
                if not has_permission(request.pp_permissions, "payment_link", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                PaymentLink.objects.filter(ref__in=ids).update(
                    status="active" if action == "activated" else "inactive", updated_date=timezone.now())
            return redirect("/admin/payment-link/")
        if "default_currency" in request.POST:
            from core.env import set_env
            set_env("payment-link-default-currency", request.POST.get("DefaultCurrency")
                    or request.POST.get("currency", ""),
                    request.POST.get("brand_id", "") or getattr(request, "pp_brand_id", "") or "both")
            messages.success(request, "Default link currency saved.")
            return redirect("/admin/payment-link/")
    from core.env import get_env
    rows = []
    for link in _paginate(request, qs):
        rows.append({"link": link, "display_status": _link_display_status(link)})
    return render(request, "staff/payment_link_list.html",
                  {"rows": rows, "status": status, "brands": Brand.objects.all(),
                   "get_env": get_env})


def _link_display_status(link: PaymentLink) -> str:
    if link.expired_date == "--":
        return link.status
    return "expired" if M.is_expired(link.expired_date) else link.status


@login_required
@page_required("payment_link")
def _save_link_fields(link: PaymentLink, request) -> None:
    """Mirror paymentLink-create/edit items[] handling -> pp_payment_link_field."""
    from core.models import PaymentLinkField
    items = {}
    for key, values in request.POST.lists():
        # items[<uid>][formType|fieldName|required|fieldID|fileExtensions|addOptions]
        import re as _re
        m = _re.match(r"items\[(.+?)\]\[(\w+)\](\[\])?", key)
        if m:
            items.setdefault(m.group(1), {}).setdefault(m.group(2), list(values))
    deleted = request.POST.getlist("deleted_items")
    if deleted:
        PaymentLinkField.objects.filter(id__in=[d for d in deleted if str(d).isdigit()]).delete()
    for uid, item in items.items():
        def one(name, default=""):
            v = item.get(name, [default])
            return v[0] if v else default

        form_type, field_name, required = one("formType"), one("fieldName"), one("required", "true")
        if not form_type or not field_name:
            continue
        value = "--"
        if form_type == "file":
            value = ", ".join(item.get("fileExtensions", []))
        elif form_type in ("select", "checkbox", "radio"):
            value = ", ".join(item.get("addOptions", []))
        field_id = one("fieldID")
        if field_id and str(field_id).isdigit():
            PaymentLinkField.objects.filter(id=field_id).update(
                formType=form_type, fieldName=field_name, required=required, value=value, updated_date=timezone.now())
        else:
            PaymentLinkField.objects.create(paymentLinkID=link.ref, formType=form_type,
                                            fieldName=field_name, required=required or "true",
                                            value=value or "--")


def payment_link_edit(request, ref: str = ""):
    from core.models import PaymentLinkField
    link = PaymentLink.objects.filter(ref=ref).first() if ref else None
    if request.method == "POST":
        need = "edit" if ref else "create"
        if not has_permission(request.pp_permissions, "payment_link", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        expiry = request.POST.get("expired_date", "")
        if expiry and expiry != "--" and not M.dateformat(expiry, "%Y-%m-%d"):
            messages.error(request, "Please enter the expiry date in the correct format (DD/MM/YYYY).")
            return redirect("/admin/payment-link/")
        title = request.POST.get("title", "")
        description = request.POST.get("description", "")
        quantity = request.POST.get("quantity", "")
        currency = request.POST.get("currency", "")
        amount = request.POST.get("amount", "")
        status = request.POST.get("status", "")
        if not title or not quantity or not description or not currency or not amount or not status:
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/payment-link/")
        product_info = request.POST.get("product_info", "")
        if title or description:
            product_info = json.dumps({"title": title, "description": description})
        def _qty(v):
            try:
                return int(float(M.money_sanitize(v or "0")))
            except (ValueError, TypeError):
                return 0

        data = {
            "brand_id": request.POST.get("brand_id", "") or getattr(request, "pp_brand_id", ""),
            "product_info": product_info or "{}",
            "amount": M.money_sanitize(amount),
            "quantity": _qty(quantity),
            "currency": currency,
            "expired_date": expiry or "--",
            "status": status,
        }
        if not all([data["brand_id"], data["currency"], data["status"]]):
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/payment-link/")
        if link:
            for k, v in data.items():
                setattr(link, k, v)
            link.save()
        else:
            link = PaymentLink.objects.create(ref=request.POST.get("ref") or M.generate_item_id(27, 27), **data)
        _save_link_fields(link, request)
        messages.success(request, "Payment link saved.")
        return redirect("/admin/payment-link/")
    fields = list(PaymentLinkField.objects.filter(paymentLinkID=ref)) if ref else []
    try:
        product = json.loads(link.product_info) if link and link.product_info else {}
    except ValueError:
        product = {}
    return render(request, "staff/payment_link_edit.html",
                  {"link": link, "brands": Brand.objects.all(), "fields": fields, "product": product})


@login_required
@page_required("gateways")
def gateway_list(request):
    qs = _scoped(Gateway.objects.all().order_by("-id"), request)
    tab = request.GET.get("tab", "").strip()
    if tab in ("mfs", "bank", "global"):
        qs = qs.filter(tab=tab)
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "gateways", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            gid = request.POST["delete"]
            GatewayParameter.objects.filter(gateway_id=gid).delete()
            Gateway.objects.filter(gateway_id=gid).delete()
            return redirect("/admin/gateways/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "gateways", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                GatewayParameter.objects.filter(gateway_id__in=ids).delete()
                Gateway.objects.filter(gateway_id__in=ids).delete()
            elif action in ("activated", "inactivated"):
                if not has_permission(request.pp_permissions, "gateways", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Gateway.objects.filter(gateway_id__in=ids).update(
                    status="active" if action == "activated" else "inactive", updated_date=timezone.now())
            return redirect("/admin/gateways/")
    tabs = {t: Gateway.objects.filter(tab=t).count() for t in ("mfs", "bank", "global")}
    return render(request, "staff/gateway_list.html",
                  {"page": _paginate(request, qs), "tab": tab, "tabs": tabs,
                   "plugins": all_gateways()})


@login_required
@page_required("gateways")
def gateway_edit(request, gateway_id: str = ""):
    from gateways.plugins import automation_extra_fields, bank_fields
    gw = Gateway.objects.filter(gateway_id=gateway_id).first() if gateway_id else None
    params = {p.option_name: p.value for p in GatewayParameter.objects.filter(gateway_id=gateway_id)} if gateway_id else {}
    plugins = all_gateways()
    is_bank_form = request.path.endswith("create-bank/") or (gw and gw.tab == "bank" and gw.slug == "bank")
    if request.method == "POST":
        need = "edit" if gateway_id else "create"
        if not has_permission(request.pp_permissions, "gateways", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        slug = request.POST.get("slug", gw.slug if gw else ("bank" if is_bank_form else ""))
        plugin = plugins.get(slug)
        if gw:
            for f in ("name", "display", "currency", "status", "tab", "logo", "primary_color",
                      "text_color", "btn_color", "btn_text_color"):
                if f in request.POST:
                    setattr(gw, f, request.POST[f])
            for f in ("min_allow", "max_allow", "fixed_discount", "percentage_discount",
                      "fixed_charge", "percentage_charge"):
                if f in request.POST:
                    setattr(gw, f, M.money_sanitize(request.POST[f]))
            if request.FILES.get("logo"):
                from core.storage import upload_image
                up = upload_image(request.FILES["logo"], 2 * 1024 * 1024)
                if up.get("status"):
                    gw.logo = f"/media/storage/{up['file']}"
            gw.save()
            gid = gw.gateway_id
        else:
            if slug and slug != "bank" and slug not in plugins:
                messages.error(request, "Invalid gateway plugin.")
                return redirect("/admin/gateways/")
            if is_bank_form:
                slug = "bank"
            gid = M.generate_item_id()
            info = plugin.info() if plugin else {}
            color = plugin.color() if plugin else {}
            gw = Gateway.objects.create(
                gateway_id=gid, brand_id=_post_brand(request), slug=slug or "bank",
                name=request.POST.get("name", info.get("title", slug or "Bank")),
                display=request.POST.get("display", info.get("title", slug or "Bank")),
                logo=request.POST.get("logo", "assets/logo.jpg"),
                currency=request.POST.get("currency", info.get("currency", "BDT")),
                tab="bank" if is_bank_form else request.POST.get("tab", info.get("tab", "mfs")),
                status=request.POST.get("status", "active"),
                min_allow=M.money_sanitize(request.POST.get("min_allow", "0")),
                max_allow=M.money_sanitize(request.POST.get("max_allow", "0")),
                fixed_discount=M.money_sanitize(request.POST.get("fixed_discount", "0")),
                percentage_discount=M.money_sanitize(request.POST.get("percentage_discount", "0")),
                fixed_charge=M.money_sanitize(request.POST.get("fixed_charge", "0")),
                percentage_charge=M.money_sanitize(request.POST.get("percentage_charge", "0")),
                primary_color=request.POST.get("primary_color", color.get("primary_color", "")),
                text_color=color.get("text_color", ""),
                btn_color=color.get("btn_color", ""),
                btn_text_color=color.get("btn_text_color", ""))
        fields = list(plugin.fields()) if plugin else []
        if plugin and plugin.info().get("gateway_type") == "automation":
            fields = automation_extra_fields() + fields
        if is_bank_form or (gw and gw.tab == "bank" and not plugin):
            fields = bank_fields()
        for field in fields:
            name = field["name"]
            if field.get("type") == "image" and request.FILES.get(name):
                from core.storage import upload_image
                up = upload_image(request.FILES[name], 5 * 1024 * 1024)
                if up.get("status"):
                    value = f"/media/storage/{up['file']}"
                else:
                    continue
            elif name in request.POST:
                if field.get("multiple"):
                    # Multi-select arrays are JSON-encoded (PHP parity).
                    value = json.dumps(request.POST.getlist(name), ensure_ascii=False)
                else:
                    value = request.POST.get(name, "")
            else:
                continue
            # Empty option values are stored as '--' (PHP parity).
            value = value if value != "" else "--"
            GatewayParameter.objects.update_or_create(gateway_id=gid, option_name=name,
                                                      defaults={"value": value, "brand_id": gw.brand_id})
        # Undeclared extra POST keys are persisted too (gateway-setting-update).
        known = {"csrf_token", "slug", "brand_id", "name", "display", "currency", "tab", "status",
                 "min_allow", "max_allow", "fixed_discount", "percentage_discount", "fixed_charge",
                 "percentage_charge", "primary_color", "text_color", "btn_color", "btn_text_color", "logo"}
        known |= {f["name"] for f in fields}
        for key in request.POST:
            if key in known:
                continue
            values = request.POST.getlist(key)
            value = values[0] if len(values) == 1 else json.dumps(values, ensure_ascii=False)
            GatewayParameter.objects.update_or_create(
                gateway_id=gid, option_name=key,
                defaults={"value": value if value != "" else "--", "brand_id": gw.brand_id})
        messages.success(request, "Gateway saved.")
        return redirect("/admin/gateways/")
    plugin = plugins.get(gw.slug) if gw else None
    fields = list(plugin.fields()) if plugin else []
    if plugin and plugin.info().get("gateway_type") == "automation":
        fields = automation_extra_fields() + fields
    if is_bank_form or (gw and gw.tab == "bank" and not plugin):
        fields = bank_fields()
    return render(request, "staff/gateway_edit.html",
                  {"gw": gw, "params": params, "plugins": plugins, "fields": fields,
                   "is_bank_form": is_bank_form, "brands": Brand.objects.all()})


@login_required
@page_required("sms_data")
def sms_data(request):
    from core.sms_engine import mfs_message_verified, sender_whitelist
    qs = SmsData.objects.exclude(device_id="00").order_by("-id")
    status = request.GET.get("status", "").strip()
    tab = request.GET.get("tab", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_until = request.GET.get("until", "").strip()
    q = request.GET.get("q", "").strip()
    if tab in ("approved", "awaiting-review", "used"):
        qs = qs.filter(status=tab)
    elif status in ("approved", "awaiting-review", "used", "error"):
        qs = qs.filter(status=status)
    else:
        qs = qs.exclude(status="error")
    if q:
        qs = qs.filter(Q(sender__icontains=q) | Q(device_id__icontains=q) | Q(trx_id__icontains=q)
                       | Q(sender_key__icontains=q) | Q(amount__icontains=q) | Q(currency__icontains=q)
                       | Q(message__icontains=q))
    if date_from:
        qs = qs.filter(created_date__gte=f"{date_from} 00:00:00")
    if date_until:
        qs = qs.filter(created_date__lte=f"{date_until} 23:59:59")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "sms_data", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            SmsData.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/sms-data/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "sms_data", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                SmsData.objects.filter(id__in=ids).delete()
            elif action in ("approved", "awaiting-review", "used"):
                if not has_permission(request.pp_permissions, "sms_data", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                SmsData.objects.filter(id__in=ids).update(status=action, updated_date=timezone.now())
            return redirect("/admin/sms-data/")
        if "edit_id" in request.POST:
            if not has_permission(request.pp_permissions, "sms_data", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            row = SmsData.objects.filter(id=request.POST["edit_id"]).first()
            if row:
                # PHP quirk (sms-data-edit): the (sender_key, trx_id) lookup must HIT
                # or the save is rejected as "Duplicate Transaction" — even on miss.
                new_sender_key = request.POST.get("sender_key", row.sender_key)
                new_status = request.POST.get("status", row.status)
                new_device = request.POST.get("device_id", "") or "--"
                new_currency = request.POST.get("currency", row.currency)
                if row.entry_type == "automatic":
                    # Frozen entry_type: re-parse the (possibly corrected) message.
                    message = request.POST.get("message", row.message or "")
                    result = mfs_message_verified(new_sender_key, message)
                    if result is False:
                        messages.error(request, "Invalid or unknown MFS message.")
                        return redirect("/admin/sms-data/")
                    new_type = result.get("type", "") or ""
                    new_amount = result.get("amount", "") or ""
                    new_number = result.get("sender", "") or ""
                    new_trx = result.get("trxid", "") or ""
                    new_balance = result.get("balance", "") or ""
                    if not new_type or not new_amount or not new_number or not new_trx:
                        messages.error(request, "Invalid or unknown MFS message.")
                        return redirect("/admin/sms-data/")
                    if not SmsData.objects.filter(sender_key=new_sender_key,
                                                  trx_id=new_trx).exists():
                        messages.error(request,
                                       "The provided Transaction ID already exists in our system.")
                        return redirect("/admin/sms-data/")
                    row.device_id = new_device
                    row.sender_key = new_sender_key
                    row.number = new_number
                    row.amount = M.money_sanitize(new_amount)
                    row.currency = new_currency
                    row.trx_id = new_trx
                    row.balance = M.money_sanitize(new_balance)
                    row.type = new_type
                    row.message = message
                    row.status = new_status
                else:
                    new_type = request.POST.get("type", row.type)
                    new_amount = request.POST.get("amount", "")
                    new_number = request.POST.get("number", "")
                    new_trx = request.POST.get("trx_id", "")
                    if not new_type or not new_amount or not new_number or not new_trx:
                        messages.error(request, "Please fill in all required fields before proceeding.")
                        return redirect("/admin/sms-data/")
                    if not SmsData.objects.filter(sender_key=new_sender_key,
                                                  trx_id=new_trx).exists():
                        messages.error(request,
                                       "The provided Transaction ID already exists in our system.")
                        return redirect("/admin/sms-data/")
                    row.device_id = new_device
                    row.sender_key = new_sender_key
                    row.number = new_number
                    row.amount = M.money_sanitize(new_amount)
                    row.currency = new_currency
                    row.trx_id = new_trx
                    row.balance = "0"
                    row.type = new_type
                    row.message = request.POST.get("message", row.message or "")
                    row.status = new_status
                row.save()
            return redirect("/admin/sms-data/")
        if not has_permission(request.pp_permissions, "sms_data", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        entry_type = request.POST.get("entry_type", "manual")
        sender_key = request.POST.get("sender_key", "")
        status_new = request.POST.get("status", "approved")
        device_id = request.POST.get("device_id", "") or "--"
        currency = request.POST.get("currency", "--")
        if entry_type == "automatic":
            message = request.POST.get("message", "")
            if not message:
                messages.error(request, "Please fill in all required fields before proceeding.")
                return redirect("/admin/sms-data/")
            result = mfs_message_verified(sender_key, message)
            if result is False:
                messages.error(request, "Invalid or unknown MFS message.")
                return redirect("/admin/sms-data/")
            sms_type = result.get("type", "") or ""
            amount = result.get("amount", "") or ""
            balance = result.get("balance", "") or ""
            number = result.get("sender", "") or ""
            trx_id = result.get("trxid", "") or ""
            if not sms_type or not amount or not number or not trx_id:
                messages.error(request, "Invalid or unknown MFS message.")
                return redirect("/admin/sms-data/")
            if SmsData.objects.filter(sender_key=sender_key, trx_id=trx_id).exists():
                messages.error(request, "The provided Transaction ID already exists in our system.")
                return redirect("/admin/sms-data/")
            SmsData.objects.create(
                source="web", device_id=device_id, sender=sender_key, sender_key=sender_key,
                simslot=request.POST.get("simslot", ""), number=number,
                amount=M.money_sanitize(amount), currency=currency, trx_id=trx_id,
                balance=M.money_sanitize(balance), message=message, type=sms_type,
                entry_type="automatic", status=status_new)
        else:
            sms_type = request.POST.get("type", "Personal")
            amount = request.POST.get("amount", "")
            number = request.POST.get("number", "")
            trx_id = request.POST.get("trx_id", "")
            if not sms_type or not amount or not number or not trx_id:
                messages.error(request, "Please fill in all required fields before proceeding.")
                return redirect("/admin/sms-data/")
            if SmsData.objects.filter(sender_key=sender_key, trx_id=trx_id).exists():
                messages.error(request, "The provided Transaction ID already exists in our system.")
                return redirect("/admin/sms-data/")
            SmsData.objects.create(
                source="web", device_id=device_id, sender=request.POST.get("sender", sender_key),
                sender_key=sender_key, simslot=request.POST.get("simslot", ""), number=number,
                amount=M.money_sanitize(amount), currency=currency, trx_id=trx_id,
                balance="0", message=request.POST.get("message", ""),
                type=sms_type, entry_type="manual", status=status_new)
        messages.success(request, "The sms data has been created successfully.")
        return redirect("/admin/sms-data/")
    return render(request, "staff/sms_data.html",
                  {"page": _paginate(request, qs), "status": status, "tab": tab, "q": q,
                   "date_from": date_from, "date_until": date_until,
                   "providers": sender_whitelist(),
                   "devices": Device.objects.filter(status="used").order_by("-id")})


@login_required
@page_required("sms_data")
def sms_data_info(request):
    """Mirror sms-data-info-byID (JSON, incl. reason)."""
    from django.http import JsonResponse
    if not has_permission(request.pp_permissions, "sms_data", "edit", request.pp_admin.role):
        return JsonResponse({"status": "false", "title": "Access denied", "message": "No permission."})
    row = SmsData.objects.filter(id=request.GET.get("id", "")).first()
    if not row:
        return JsonResponse({"status": "false", "title": "Request Failed", "message": "Invalid request"})
    return JsonResponse({"status": "true", "device_id": row.device_id, "sender_key": row.sender_key,
                         "number": row.number, "amount": str(row.amount), "currency": row.currency,
                         "trx_id": row.trx_id, "balance": str(row.balance), "message": row.message or "",
                         "type": row.type, "entry_type": row.entry_type, "istatus": row.status,
                         "reason": row.reason or ""})


@login_required
@page_required("device")
def devices(request):
    from datetime import timedelta
    from django.utils import timezone as _tz
    qs = Device.objects.filter(status="used").order_by("-id")
    conn = request.GET.get("connected", "").strip()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(model__icontains=q))
    if conn == "connected":
        qs = qs.filter(updated_date__gte=_tz.now() - timedelta(minutes=6))
    elif conn == "disconnected":
        qs = qs.filter(updated_date__lt=_tz.now() - timedelta(minutes=6))
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "device", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            dev = Device.objects.filter(id=request.POST["delete"]).first()
            if dev:
                BalanceVerification.objects.filter(device_id=dev.device_id).delete()
                dev.delete()
            return redirect("/admin/devices/")
        if "bulk" in request.POST and request.POST.get("bulk") == "deleted":
            if not has_permission(request.pp_permissions, "device", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            devs = list(Device.objects.filter(id__in=request.POST.getlist("selected_ids")))
            BalanceVerification.objects.filter(
                device_id__in=[d.device_id for d in devs]).delete()
            Device.objects.filter(id__in=[d.id for d in devs]).delete()
            return redirect("/admin/devices/")
    from django.conf import settings as _s
    base_url = _s.ALLOWED_HOSTS[0] if _s.ALLOWED_HOSTS and _s.ALLOWED_HOSTS[0] != "*" else request.get_host()
    return render(request, "staff/devices.html", {"page": _paginate(request, qs), "base_url": base_url,
                                                     "connected": conn, "q": q})


@login_required
@page_required("device")
def device_connect_info(request):
    """Mirror device-connect-info: OTP create/rotate for this admin (JSON)."""
    from django.http import JsonResponse
    if not has_permission(request.pp_permissions, "device", "connect", request.pp_admin.role):
        return JsonResponse({"status": "false", "title": "Access denied",
                             "message": "You need permission to perform this action. Please contact the admin."})
    otp = M.generate_item_id()
    # device.d_id = the pp_admin browser cookie (mirrors $pp_admin = getCookie('pp_admin')).
    d_id = request.session.get("pp_cookie", "") or ""
    row = Device.objects.filter(status="processing", d_id=d_id).first()
    if row:
        row.otp = otp
        row.save(update_fields=["otp", "updated_date"])
    else:
        Device.objects.create(d_id=d_id, device_id=M.generate_item_id(), otp=otp)
    return JsonResponse({"status": "true", "otp": otp})


@login_required
@page_required("device")
def balance_verification(request):
    from core.sms_engine import sender_whitelist
    d_id = request.GET.get("d_id", "").strip()
    device = Device.objects.filter(device_id=d_id).first() if d_id else None
    qs = BalanceVerification.objects.all().order_by("-id")
    if d_id:
        qs = qs.filter(device_id=d_id)
    bq = request.GET.get("q", "").strip()
    bstatus = request.GET.get("status", "").strip()
    if bq:
        qs = qs.filter(Q(sender_key__icontains=bq) | Q(type__icontains=bq) | Q(current_balance__icontains=bq))
    if bstatus in ("active", "inactive"):
        qs = qs.filter(status=bstatus)
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "device", "balance_verification_for", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        if "delete" in request.POST:
            BalanceVerification.objects.filter(id=request.POST["delete"]).delete()
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                BalanceVerification.objects.filter(id__in=ids).delete()
            elif action in ("activated", "inactivated"):
                BalanceVerification.objects.filter(id__in=ids).update(
                    status="active" if action == "activated" else "inactive", updated_date=timezone.now())
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        if "edit_id" in request.POST:
            row = BalanceVerification.objects.filter(id=request.POST["edit_id"]).first()
            if row:
                new_sender = request.POST.get("sender_key", row.sender_key)
                new_type = request.POST.get("type", row.type)
                if BalanceVerification.objects.filter(device_id=row.device_id, sender_key=new_sender,
                                                      type=new_type).exclude(id=row.id).exists():
                    messages.error(request, "Verification rule already exists for this device and method.")
                    return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
                row.sender_key = new_sender
                row.type = new_type
                row.current_balance = M.money_sanitize(request.POST.get("current_balance", row.current_balance))
                row.simslot = request.POST.get("simslot", row.simslot)
                row.status = request.POST.get("status", row.status)
                row.save()
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        if "iupdate" in request.POST:
            # balance-only quick update (always success).
            row = BalanceVerification.objects.filter(id=request.POST["iupdate"]).first()
            if row:
                row.current_balance = M.money_sanitize(request.POST.get("current_balance", "0") or "0")
                row.save(update_fields=["current_balance", "updated_date"])
            messages.success(request, "Balance updated.")
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        # create: all fields required, strict active|inactive, used parent, no dup
        device_id = request.POST.get("device_id", d_id or "")
        sender_key = request.POST.get("sender_key", "")
        btype = request.POST.get("type", "")
        simslot = request.POST.get("simslot", "")
        bstatus = request.POST.get("status", "")
        balance = request.POST.get("current_balance", "")
        if not device_id or not sender_key or not btype or not simslot or not bstatus or balance == "":
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        if bstatus not in ("active", "inactive"):
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        parent = Device.objects.filter(device_id=device_id, status="used").first()
        if not parent:
            messages.error(request, "Device must be paired (used) before adding verification.")
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        if BalanceVerification.objects.filter(device_id=device_id, sender_key=sender_key,
                                              type=btype).exists():
            messages.error(request, "Verification rule already exists for this device and method.")
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        BalanceVerification.objects.create(
            device_id=device_id, sender_key=sender_key, type=btype,
            current_balance=M.money_sanitize(balance),
            simslot=simslot, status=bstatus)
        messages.success(request, "Balance verification rule created.")
        return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
    providers = {k: v for k, v in sender_whitelist().items() if v.get("balance_verify") == "true"}
    page = _paginate(request, qs)
    for row in page:
        info = sender_whitelist(provider_key=row.sender_key)
        row.provider_name = info.get("name", row.sender_key) if info else row.sender_key
        row.provider_currency = info.get("currency", "") if info else ""
    return render(request, "staff/balance_verification.html",
                  {"page": page, "d_id": d_id, "device": device, "providers": providers,
                   "bq": bq, "bstatus": bstatus})


@login_required
@page_required("device")
def balance_verification_info(request):
    """Mirror balance-verification-info-byID (JSON)."""
    from django.http import JsonResponse
    if not has_permission(request.pp_permissions, "device", "balance_verification_for",
                          request.pp_admin.role):
        return JsonResponse({"status": "false", "title": "Access denied", "message": "No permission."})
    row = BalanceVerification.objects.filter(id=request.GET.get("id", "")).first()
    if not row:
        return JsonResponse({"status": "false", "title": "Request Failed", "message": "Invalid request"})
    return JsonResponse({"status": "true", "sender_key": row.sender_key, "type": row.type,
                         "current_balance": M.money_round(str(row.current_balance)),
                         "simslot": row.simslot, "istatus": row.status})


@login_required
@page_required("domains")
def domains(request):
    qs = Domain.objects.all().order_by("-id")
    dq = request.GET.get("q", "").strip()
    dstatus = request.GET.get("status", "").strip()
    dfrom = request.GET.get("from", "").strip()
    duntil = request.GET.get("until", "").strip()
    if dq:
        qs = qs.filter(domain__icontains=dq)
    if dstatus in ("active", "inactive"):
        qs = qs.filter(status=dstatus)
    if dfrom:
        qs = qs.filter(created_date__gte=f"{dfrom} 00:00:00")
    if duntil:
        qs = qs.filter(created_date__lte=f"{duntil} 23:59:59")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "domains", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Domain.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/domains/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "domains", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Domain.objects.filter(id__in=ids).delete()
            elif action in ("activated", "inactivated", "inactive"):
                if not has_permission(request.pp_permissions, "domains", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Domain.objects.filter(id__in=ids).update(
                    status="active" if action == "activated" else "inactive", updated_date=timezone.now())
            return redirect("/admin/domains/")
        if "edit_id" in request.POST:
            if not has_permission(request.pp_permissions, "domains", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            row = Domain.objects.filter(id=request.POST["edit_id"]).first()
            if row:
                domain = M.get_domain_value(request.POST.get("domain", row.domain))
                if not domain:
                    messages.error(request, "Please enter a valid domain.")
                    return redirect("/admin/domains/")
                if Domain.objects.filter(domain=domain).exclude(id=row.id).exists():
                    messages.error(request, "Domain already exists.")
                    return redirect("/admin/domains/")
                row.domain = domain
                row.status = request.POST.get("status", row.status)
                row.save()
            return redirect("/admin/domains/")
        if not has_permission(request.pp_permissions, "domains", "whitelist", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        domain = M.get_domain_value(request.POST.get("domain", ""))
        if not domain:
            messages.error(request, "Please enter a valid domain.")
            return redirect("/admin/domains/")
        if Domain.objects.filter(domain=domain).exists():
            messages.error(request, "Domain already exists.")
            return redirect("/admin/domains/")
        Domain.objects.create(domain=domain, status=request.POST.get("status", "active"))
        return redirect("/admin/domains/")
    return render(request, "staff/domains.html", {"page": _paginate(request, qs), "q": dq,
                                                     "status": dstatus, "date_from": dfrom,
                                                     "date_until": duntil})


def _brand_protected(brand: Brand, request) -> bool:
    """id==1 (first row) and the current brand cannot be deleted."""
    first = Brand.objects.order_by("id").first()
    return bool(first and brand.id == first.id) or \
        brand.brand_id == request.session.get("pp_brand_id", "")


def _delete_brand_cascade(brand: Brand) -> None:
    """Mirror brand-delete cascade across all 13 brand-owned tables."""
    from core.models import (ApiKey, Currency, Customer, EnvOption, Faq,
                             Gateway, GatewayParameter, Invoice, InvoiceItem, PaymentLink,
                             PaymentLinkField, Permission, Transaction, WebhookLog)
    refs = Invoice.objects.filter(brand_id=brand.brand_id).values_list("ref", flat=True)
    InvoiceItem.objects.filter(invoice_id__in=list(refs)).delete()
    Invoice.objects.filter(brand_id=brand.brand_id).delete()
    link_refs = PaymentLink.objects.filter(brand_id=brand.brand_id).values_list("ref", flat=True)
    PaymentLinkField.objects.filter(paymentLinkID__in=list(link_refs)).delete()
    PaymentLink.objects.filter(brand_id=brand.brand_id).delete()
    gw_ids = Gateway.objects.filter(brand_id=brand.brand_id).values_list("gateway_id", flat=True)
    GatewayParameter.objects.filter(gateway_id__in=list(gw_ids)).delete()
    Gateway.objects.filter(brand_id=brand.brand_id).delete()
    ApiKey.objects.filter(brand_id=brand.brand_id).delete()
    Currency.objects.filter(brand_id=brand.brand_id).delete()
    Customer.objects.filter(brand_id=brand.brand_id).delete()
    EnvOption.objects.filter(brand_id=brand.brand_id).delete()
    Faq.objects.filter(brand_id=brand.brand_id).delete()
    Permission.objects.filter(brand_id=brand.brand_id).delete()
    Transaction.objects.filter(brand_id=brand.brand_id).delete()
    WebhookLog.objects.filter(brand_id=brand.brand_id).delete()
    brand.delete()


@login_required
@page_required("brands")
def brand_list(request):
    qs = Brand.objects.all().order_by("-id")
    q = request.GET.get("q", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_until = request.GET.get("until", "").strip()
    if q:
        qs = qs.filter(identify_name__icontains=q)
    if date_from:
        qs = qs.filter(created_date__gte=f"{date_from} 00:00:00")
    if date_until:
        qs = qs.filter(created_date__lte=f"{date_until} 23:59:59")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "brands", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            brand = Brand.objects.filter(brand_id=request.POST["delete"]).first()
            if brand and not _brand_protected(brand, request):
                _delete_brand_cascade(brand)
            return redirect("/admin/brands/")
        if "bulk" in request.POST and request.POST.get("bulk") == "deleted":
            if not has_permission(request.pp_permissions, "brands", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            for brand in Brand.objects.filter(brand_id__in=request.POST.getlist("selected_ids")):
                if not _brand_protected(brand, request):
                    _delete_brand_cascade(brand)
            return redirect("/admin/brands/")
    first = Brand.objects.order_by("id").first()
    rows = []
    for brand in _paginate(request, qs):
        rows.append({"brand": brand,
                     "deleteable": not _brand_protected(brand, request)})
    return render(request, "staff/brand_list.html",
                  {"rows": rows, "page": _paginate(request, qs), "q": q,
                   "date_from": date_from, "date_until": date_until,
                   "first_id": first.id if first else None,
                   "current_brand": request.session.get("pp_brand_id", "")})


@login_required
@page_required("brands")
def brand_edit(request, brand_id: str = ""):
    brand = Brand.objects.filter(brand_id=brand_id).first() if brand_id else None
    if request.method == "POST":
        need = "edit" if brand_id else "create"
        if not has_permission(request.pp_permissions, "brands", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        if brand_id and not brand:
            return render(request, "core/404.html", status=404)
        if brand:
            # edit-brand is rename-only; the default (first) brand and the
            # current brand cannot be renamed; names must stay unique.
            first = Brand.objects.order_by("id").first()
            if first and brand.id == first.id:
                messages.error(request, "The default brand cannot be renamed.")
                return redirect("/admin/brands/")
            if brand.brand_id == request.session.get("pp_brand_id", ""):
                messages.error(request, "The current brand cannot be renamed.")
                return redirect("/admin/brands/")
            new_name = (request.POST.get("identify_name", "") or "").strip() or brand.identify_name
            if Brand.objects.filter(identify_name=new_name).exclude(id=brand.id).exists():
                messages.error(request, "Brand name already exists.")
                return redirect("/admin/brands/")
            brand.identify_name = new_name
            brand.save()
        else:
            # create-new-brand: single name + auto-seed permission + currency rows.
            name = (request.POST.get("identify_name") or "").strip()
            if not name:
                messages.error(request, "Please fill in all required fields before proceeding.")
                return redirect("/admin/brands/")
            if Brand.objects.filter(identify_name=name).exists():
                messages.error(request, "Brand name already exists.")
                return redirect("/admin/brands/")
            new_id = M.generate_item_id()
            brand = Brand.objects.create(brand_id=new_id, identify_name=name)
            Permission.objects.create(brand_id=new_id, a_id=request.pp_admin.a_id,
                                      permission=json.dumps(permission_schema()))
            # Fan-out: every admin gets a permission row for the new brand.
            for admin_user in Admin.objects.filter(role="admin").exclude(a_id=request.pp_admin.a_id):
                Permission.objects.get_or_create(
                    brand_id=new_id, a_id=admin_user.a_id,
                    defaults={"permission": json.dumps(permission_schema())})
            Currency.objects.get_or_create(brand_id=new_id, code="BDT",
                                           defaults={"symbol": "৳", "rate": "0"})
            request.session["pp_brand_id"] = new_id
            messages.success(request, "Brand created.")
        return redirect("/admin/brands/")
    return render(request, "staff/brand_edit.html", {"brand": brand})


BRAND_GENERAL_FIELDS = ("identify_name", "name", "support_email_address", "support_phone_number",
    "support_website", "whatsapp_number", "telegram", "facebook_messenger", "facebook_page",
    "street_address", "city_town", "postal_code", "country", "timezone", "language",
    "currency_code", "autoExchange", "payment_tolerance")


@login_required
@page_required("brand_settings")
def brand_general(request):
    brand = Brand.objects.filter(brand_id=getattr(request, "pp_brand_id", "")).first() or Brand.objects.first()
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "brand_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        required = ("identify_name", "timezone", "language", "currency_code", "payment_tolerance",
                    "autoExchange")
        if brand and any(not request.POST.get(f, "") for f in required):
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/brand-setting/general/")
        if brand:
            for f in BRAND_GENERAL_FIELDS:
                if f in request.POST:
                    setattr(brand, f, request.POST[f])
            brand.payment_tolerance = M.money_sanitize(request.POST.get("payment_tolerance", brand.payment_tolerance or "0"))
            from core.storage import delete_image, upload_image
            for upload_field, post_key in (("favicon", "favicon"), ("logo", "primary_logo")):
                uploaded = request.FILES.get(upload_field) or request.FILES.get(post_key)
                if uploaded:
                    up = upload_image(uploaded, 2 * 1024 * 1024)
                    if up.get("status"):
                        old = getattr(brand, upload_field, "")
                        if old and old not in ("--", ""):
                            delete_image(old)
                        setattr(brand, upload_field, f"/media/storage/{up['file']}")
            brand.save()
        messages.success(request, "General settings saved.")
        return redirect("/admin/brand-setting/general/")
    import zoneinfo
    return render(request, "staff/brand_general.html",
                  {"brand": brand, "timezones": sorted(zoneinfo.available_timezones())})


@login_required
@page_required("brand_settings")
def brand_api(request):
    keys = _scoped(ApiKey.objects.all().order_by("-id"), request)
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "api_settings", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            ApiKey.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/brand-setting/api/")
        if "bulk" in request.POST:
            if not has_permission(request.pp_permissions, "api_settings", "view", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "api_settings", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                ApiKey.objects.filter(id__in=ids).delete()
            elif action in ("activated", "inactivated"):
                if not has_permission(request.pp_permissions, "api_settings", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                ApiKey.objects.filter(id__in=ids).update(
                    status="active" if action == "activated" else "inactive", updated_date=timezone.now())
            return redirect("/admin/brand-setting/api/")
        if "edit_id" in request.POST:
            if not has_permission(request.pp_permissions, "api_settings", "view", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            if not has_permission(request.pp_permissions, "api_settings", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            key = ApiKey.objects.filter(id=request.POST["edit_id"]).first()
            if key:
                name = request.POST.get("name", key.name)
                if ApiKey.objects.filter(brand_id=key.brand_id, name=name).exclude(id=key.id).exists():
                    messages.error(request, "An API key with this name already exists for the brand.")
                    return redirect("/admin/brand-setting/api/")
                key.name = name
                expired = (request.POST.get("expired_date") or "").strip()
                if expired and not M.dateformat(expired, "%Y-%m-%d"):
                    messages.error(request, "Expiry date must be YYYY-MM-DD.")
                    return redirect("/admin/brand-setting/api/")
                key.expired_date = expired or "--"
                key.status = request.POST.get("status", key.status)
                key.api_scopes = json.dumps(request.POST.getlist("scopes"))
                key.save()
            return redirect("/admin/brand-setting/api/")
        if not has_permission(request.pp_permissions, "api_settings", "view", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        if not has_permission(request.pp_permissions, "api_settings", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        import secrets as _s
        brand_id = _post_brand(request)
        name = request.POST.get("name", "")
        if not name:
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/brand-setting/api/")
        if ApiKey.objects.filter(brand_id=brand_id, name=name).exists():
            messages.error(request, "An API key with this name already exists for the brand.")
            return redirect("/admin/brand-setting/api/")
        expired = (request.POST.get("expired_date") or "").strip()
        if expired and not M.dateformat(expired, "%Y-%m-%d"):
            messages.error(request, "Expiry date must be YYYY-MM-DD.")
            return redirect("/admin/brand-setting/api/")
        ApiKey.objects.create(brand_id=brand_id, name=name,
                              api_key=_s.token_hex(25),
                              expired_date=expired or "--",
                              api_scopes=json.dumps(request.POST.getlist("scopes")),
                              status=request.POST.get("status", "active"))
        messages.success(request, "API key created.")
        return redirect("/admin/brand-setting/api/")
    rows = [{"key": k, "display_status": ("expired" if k.expired_date not in ("--", "", None)
                                             and M.is_expired(k.expired_date) else k.status)}
              for k in keys]
    return render(request, "staff/brand_api.html", {"rows": rows, "keys": keys, "brands": Brand.objects.all()})


@login_required
@page_required("brand_settings")
def brand_currency(request):
    qs = list(_scoped(Currency.objects.all().order_by("brand_id", "code"), request))
    _base = {c.brand_id: (Brand.objects.filter(brand_id=c.brand_id).first().currency_code
                          if Brand.objects.filter(brand_id=c.brand_id).first() else "")
             for c in qs}
    qs = sorted(qs, key=lambda c: (c.brand_id, c.code != (_base.get(c.brand_id) or ""), c.code))
    if request.method == "POST":
        if "sync" in request.POST:
            # currency-rateSync: refresh one row from the FX source.
            if not has_permission(request.pp_permissions, "currency_settings", "sync_rate",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            err = _sync_currency_rate(request.POST["sync"])
            if err:
                messages.error(request, err)
            else:
                messages.success(request, "The selected currency rate have been updated successfully.")
            return redirect("/admin/brand-setting/currency/")
        if "bulk_sync" in request.POST:
            if not has_permission(request.pp_permissions, "currency_settings", "sync_rate",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            qs = Currency.objects.all()
            if getattr(request, "pp_brand_id", ""):
                qs = qs.filter(brand_id=request.pp_brand_id)
            for row in qs:
                _sync_currency_rate(row.id)
            messages.success(request, "Currency rates updated.")
            return redirect("/admin/brand-setting/currency/")
        if "bulk_import" in request.POST:
            if not has_permission(request.pp_permissions, "currency_settings", "import",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            result = _import_default_currencies(_post_brand(request))
            (messages.success if result.get("status") == "true" else messages.error)(
                request, f"{result.get('title')}: {result.get('message')}")
            return redirect("/admin/brand-setting/currency/")
        if not has_permission(request.pp_permissions, "currency_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        if "edit_id" in request.POST:
            row = Currency.objects.filter(id=request.POST["edit_id"]).first()
            if row and (not getattr(request, "pp_brand_id", "") or row.brand_id == request.pp_brand_id):
                symbol, rate = request.POST.get("symbol", ""), request.POST.get("rate", "")
                if not row or not symbol or not rate:
                    messages.error(request, "Please fill in all required fields before proceeding.")
                    return redirect("/admin/brand-setting/currency/")
                row.symbol = symbol
                row.rate = M.money_sanitize(rate)
                row.save()
        else:
            Currency.objects.update_or_create(brand_id=_post_brand(request), code=request.POST.get("code", ""),
                                              defaults={"symbol": request.POST.get("symbol", ""),
                                                        "rate": M.money_sanitize(request.POST.get("rate", "0"))})
        return redirect("/admin/brand-setting/currency/")
    return render(request, "staff/brand_currency.html", {"currencies": qs, "brands": Brand.objects.all()})


def _sync_currency_rate(currency_pk) -> str | None:
    """Mirror currency-rateSync: rate = 1/source-rate for the brand base currency.
    Returns an error message or None on success/no-op."""
    import requests
    row = Currency.objects.filter(id=currency_pk).first()
    if not row:
        return None
    brand = Brand.objects.filter(brand_id=row.brand_id).first()
    base = (brand.currency_code if brand else "BDT").lower()
    try:
        data = requests.get(
            f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.json",
            timeout=10, verify=False).json()
    except Exception:
        return None
    rates = data.get(base, {})
    if not isinstance(rates, dict):
        return "Invalid default currency"
    rate = rates.get(row.code.lower(), 0)
    if not rate or rate <= 0:
        return None
    row.rate = M.money_div("1", M.money_sanitize(f"{rate:.14f}"))
    row.save(update_fields=["rate", "updated_date"])
    return None


def _import_default_currencies(brand_id: str) -> dict:
    """Mirror currency-bulkImport: gist list, symbol_native, rate 0, skip existing."""
    import requests
    try:
        data = requests.get("https://gist.githubusercontent.com/ksafranski/2973986/raw/",
                            timeout=10, verify=False).json()
    except Exception as e:
        return {"status": "false", "title": "Request Failed", "message": f"Import failed: {e}"}
    if not isinstance(data, dict):
        return {"status": "false", "title": "Request Failed", "message": "Invalid request"}
    for code, details in data.items():
        if not Currency.objects.filter(brand_id=brand_id, code=code).exists():
            symbol = details.get("symbol_native", code) if isinstance(details, dict) else code
            Currency.objects.create(brand_id=brand_id, code=code, symbol=symbol, rate="0")
    return {"status": "true", "title": "Currencies Imported",
            "message": "All currency data has been imported successfully."}


@login_required
@page_required("brand_settings")
def brand_faq(request):
    qs = _scoped(Faq.objects.all().order_by("-id"), request)
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "faq_settings", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Faq.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/brand-setting/faq/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "faq_settings", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Faq.objects.filter(id__in=ids).delete()
            elif action in ("activated", "inactivated"):
                if not has_permission(request.pp_permissions, "faq_settings", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Faq.objects.filter(id__in=ids).update(
                    status="active" if action == "activated" else "inactive", updated_date=timezone.now())
            return redirect("/admin/brand-setting/faq/")
        if "edit_id" in request.POST:
            if not has_permission(request.pp_permissions, "faq_settings", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            row = Faq.objects.filter(id=request.POST["edit_id"]).first()
            if row:
                row.title = request.POST.get("title", row.title)
                row.description = request.POST.get("description", row.description)
                row.status = request.POST.get("status", row.status)
                row.save()
            return redirect("/admin/brand-setting/faq/")
        if not has_permission(request.pp_permissions, "faq_settings", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        title, description, status = (request.POST.get("title", ""), request.POST.get("description", ""),
                                      request.POST.get("status", ""))
        if not title or not description or not status:
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/brand-setting/faq/")
        Faq.objects.create(brand_id=_post_brand(request), title=title,
                           description=description, status=status)
        return redirect("/admin/brand-setting/faq/")
    return render(request, "staff/brand_faq.html", {"faqs": qs, "brands": Brand.objects.all()})


@login_required
@page_required("brand_settings")
def brand_themes(request):
    from themes.twenty_six import THEMES, TwentySixTheme
    gallery = {slug: cls().info() for slug, cls in THEMES.items()}
    current_brand = Brand.objects.filter(brand_id=getattr(request, "pp_brand_id", "")).first() \
        or Brand.objects.first()
    active_slug = (current_brand.theme if current_brand and current_brand.theme else "twenty-six") \
        or "twenty-six"
    theme = THEMES.get(active_slug, TwentySixTheme)()
    if request.method == "POST":
        if "activate" in request.POST:
            # themes-new-active: UPDATE current brand row only.
            if not has_permission(request.pp_permissions, "theme_settings", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            slug = request.POST["activate"]
            if slug in THEMES and current_brand:
                current_brand.theme = slug
                current_brand.save(update_fields=["theme", "updated_date"])
                messages.success(request, "Theme activated.")
            return redirect("/admin/brand-setting/themes/")
        if not has_permission(request.pp_permissions, "theme_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        # theme-setting-update: EVERY posted key saved as <slug>-<key>
        # (undeclared keys included), arrays JSON-encoded, files at 5MB.
        target_id = request.POST.get("brand_id", "") or (current_brand.brand_id if current_brand else "both")
        target = Brand.objects.filter(brand_id=target_id).first()
        slug = (target.theme if target and target.theme else active_slug) or active_slug
        skip = {"csrf_token", "brand_id", "activate"}
        for key in request.POST:
            if key in skip:
                continue
            values = request.POST.getlist(key)
            value = values[0] if len(values) == 1 else json.dumps(values, ensure_ascii=False)
            set_env(f"{slug}-{key}", value, target_id)
        for key, uploaded in request.FILES.items():
            from core.storage import upload_image
            up = upload_image(uploaded, 5 * 1024 * 1024)
            if up.get("status"):
                set_env(f"{slug}-{key}", f"/media/storage/{up['file']}", target_id)
        messages.success(request, "Theme settings saved.")
        return redirect("/admin/brand-setting/themes/")
    saved = {f["name"]: get_env(f"{active_slug}-{f['name']}",
                                current_brand.brand_id if current_brand else "both")
             for f in theme.fields()}
    return render(request, "staff/brand_themes.html",
                  {"fields": theme.fields(), "saved": saved, "brands": Brand.objects.all(),
                   "gallery": gallery, "active_slug": active_slug})


@login_required
@page_required("staff_management")
def staff_list(request):
    # staff-management-list scopes role=staff and excludes self.
    qs = Admin.objects.filter(role="staff").exclude(a_id=request.pp_admin.a_id).order_by("-id")
    status = request.GET.get("status", "").strip()
    q = request.GET.get("q", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_until = request.GET.get("until", "").strip()
    if status in ("active", "suspend"):
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(username__icontains=q) | Q(email__icontains=q))
    if date_from:
        qs = qs.filter(created_date__gte=f"{date_from} 00:00:00")
    if date_until:
        qs = qs.filter(created_date__lte=f"{date_until} 23:59:59")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "staff", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            target = Admin.objects.filter(a_id=request.POST["delete"], role="staff").first()
            if target:
                if target.a_id == request.pp_admin.a_id:
                    messages.error(request, "You cannot delete your own account.")
                    return redirect("/admin/staff/")
                _delete_staff(target)
            return redirect("/admin/staff/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = [i for i in request.POST.getlist("selected_ids") if i != request.pp_admin.a_id]
            if action == "deleted":
                if not has_permission(request.pp_permissions, "staff", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                for target in Admin.objects.filter(a_id__in=ids, role="staff"):
                    _delete_staff(target)
            elif action in ("activated", "suspended"):
                if not has_permission(request.pp_permissions, "staff", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Admin.objects.filter(a_id__in=ids, role="staff").update(
                    status="active" if action == "activated" else "suspend", updated_date=timezone.now())
            return redirect("/admin/staff/")
    return render(request, "staff/staff_list.html",
                  {"page": _paginate(request, qs), "status": status, "q": q,
                   "date_from": date_from, "date_until": date_until})


def _delete_staff(member: Admin) -> None:
    """Mirror staff-delete/bulk cascade: permission + browser_log + admin rows."""
    Permission.objects.filter(a_id=member.a_id).delete()
    BrowserLog.objects.filter(a_id=member.a_id).delete()
    member.delete()


@login_required
@page_required("staff_management")
def staff_edit(request, a_id: str = ""):
    member = Admin.objects.filter(a_id=a_id, role="staff").first() if a_id else None
    if a_id and not member:
        return render(request, "core/404.html", status=404)
    if member and member.a_id == request.pp_admin.a_id:
        messages.error(request, "You are not allowed to edit your own staff information.")
        return redirect("/admin/staff/")
    brands = Brand.objects.all()
    assigned = []
    if member:
        assigned = [p.brand_id for p in Permission.objects.filter(a_id=a_id)]
    if request.method == "POST":
        need = "edit" if a_id else "create"
        if not has_permission(request.pp_permissions, "staff", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        from django.core.exceptions import ValidationError as _VE
        from django.core.validators import validate_email as _ve

        def _matrix_or_full():
            # Posted res_*/page_* fields filtered through the schema
            # (staff-create builds permissions_json the same way).
            schema = permission_schema()
            posted = [k for k in request.POST if k.startswith("res_") or k.startswith("page_")]
            if not posted:
                return json.dumps(schema)
            new = {"resources": {}, "pages": {}}
            for module, actions in schema["resources"].items():
                new["resources"][module] = {a: bool(request.POST.get(f"res_{module}_{a}"))
                                            for a in actions}
            for page in schema["pages"]:
                new["pages"][page] = bool(request.POST.get(f"page_{page}"))
            return json.dumps(new)

        if member:
            username = request.POST.get("username", member.username)
            email = request.POST.get("email", member.email)
            full_name = request.POST.get("full_name", member.full_name)
            if not full_name or not username or not email:
                messages.error(request, "Please fill in all required fields before proceeding.")
                return redirect(f"/admin/staff/{a_id}/")
            try:
                _ve(email)
            except _VE:
                messages.error(request, "Please enter a valid email address.")
                return redirect(f"/admin/staff/{a_id}/")
            if Admin.objects.filter(username=username).exclude(a_id=member.a_id).exists():
                messages.error(request, "Username already exists.")
                return redirect(f"/admin/staff/{a_id}/")
            if Admin.objects.filter(email=email).exclude(a_id=member.a_id).exists():
                messages.error(request, "Email already exists.")
                return redirect(f"/admin/staff/{a_id}/")
            member.full_name = full_name
            member.username = username
            member.email = email
            # PHP staff-update never writes status; activate/suspend is bulk-only.
            if request.POST.get("password"):
                member.password = make_password(request.POST["password"])
                member.temp_password = make_password(M.generate_strong_password())
            member.save()
        else:
            username, email = request.POST.get("username", ""), request.POST.get("email", "")
            full_name = request.POST.get("full_name", "")
            password = request.POST.get("password", "")
            brand_list = request.POST.getlist("brands")
            if not full_name or not username or not email or not password:
                messages.error(request, "Please fill in all required fields before proceeding.")
                return redirect("/admin/staff/create/")
            try:
                _ve(email)
            except _VE:
                messages.error(request, "Please enter a valid email address.")
                return redirect("/admin/staff/create/")
            if not brand_list:
                messages.error(request, "You need to allow minimum 1 brand.")
                return redirect("/admin/staff/create/")
            if Admin.objects.filter(username=username).exists():
                messages.error(request, "Username already exists.")
                return redirect("/admin/staff/create/")
            if Admin.objects.filter(email=email).exists():
                messages.error(request, "Email already exists.")
                return redirect("/admin/staff/create/")
            member = Admin.objects.create(a_id=M.generate_item_id(), full_name=full_name,
                                          username=username, email=email,
                                          password=make_password(password),
                                          temp_password=make_password(M.generate_strong_password()),
                                          role="staff", status=request.POST.get("status", "active"))
        # brand assignment (assign_brand_to): only existing brands get rows.
        Permission.objects.filter(a_id=member.a_id).delete()
        matrix = _matrix_or_full()
        for b in request.POST.getlist("brands"):
            if Brand.objects.filter(brand_id=b).exists():
                Permission.objects.create(brand_id=b, a_id=member.a_id, permission=matrix)
        messages.success(request, "Staff saved.")
        return redirect("/admin/staff/")
    schema = permission_schema()
    return render(request, "staff/staff_edit.html", {"member": member, "brands": brands, "assigned": assigned, "schema": schema})


@login_required
@page_required("staff_management")
def staff_permission_list(request, a_id: str):
    """Mirror permissions-list: per-staff brand grants + add-brand + bulk."""
    member = Admin.objects.filter(a_id=a_id, role="staff").first()
    if not member:
        return render(request, "core/404.html", status=404)
    if not has_permission(request.pp_permissions, "staff", "view_permission_list",
                          request.pp_admin.role):
        return render(request, "staff/denied.html", status=403)
    if member.a_id == request.pp_admin.a_id:
        messages.error(request, "You can't edit your info.")
        return redirect("/admin/staff/")
    rows = Permission.objects.filter(a_id=a_id).order_by("id")
    if request.method == "POST":
        if "add_brand" in request.POST:
            if not has_permission(request.pp_permissions, "staff", "assign_brand_to", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            brand_id = request.POST.get("brand_id", "")
            if not brand_id:
                messages.error(request, "You need to allow minimum 1 brand to create a permission.")
                return redirect(f"/admin/staff/{a_id}/brands/")
            if brand_id and not rows.filter(brand_id=brand_id).exists():
                Permission.objects.create(brand_id=brand_id, a_id=a_id,
                                          permission=json.dumps(permission_schema()))
            return redirect(f"/admin/staff/{a_id}/brands/")
        if "delete" in request.POST:
            # PHP checks the `delete_permission` key (absent from schema, so
            # staff are always denied and only admins pass).
            if not has_permission(request.pp_permissions, "staff", "delete_permission",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            target = rows.filter(id=request.POST["delete"]).first()
            if target and target.brand_id == request.session.get("pp_brand_id", "") and \
                    target.a_id == request.pp_admin.a_id:
                messages.error(request, "You cannot delete your own permission.")
                return redirect(f"/admin/staff/{a_id}/brands/")
            rows.filter(id=request.POST["delete"]).delete()
            return redirect(f"/admin/staff/{a_id}/brands/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "staff", "delete_permission",
                                      request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                # Skip rows owned by self or an admin (PHP parity).
                admin_ids = set(Admin.objects.filter(role="admin").values_list("a_id", flat=True))
                for row in Permission.objects.filter(id__in=ids, a_id=a_id):
                    if row.a_id == request.pp_admin.a_id or row.a_id in admin_ids:
                        continue
                    row.delete()
            elif action in ("activated", "suspended"):
                if not has_permission(request.pp_permissions, "staff", "edit_permission",
                                      request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                rows.filter(id__in=ids).update(
                    status="active" if action == "activated" else "suspend", updated_date=timezone.now())
            return redirect(f"/admin/staff/{a_id}/brands/")
    return render(request, "staff/staff_permission_list.html",
                  {"member": member, "rows": rows, "brands": Brand.objects.all()})


@login_required
@page_required("staff_management")
def staff_permissions(request, a_id: str):
    """Mirror edit-permissions: addressed by permission-row id (?permission=), self-block."""
    member = Admin.objects.filter(a_id=a_id, role="staff").first()
    if not member:
        return render(request, "core/404.html", status=404)
    if member.a_id == request.pp_admin.a_id:
        messages.error(request, "You cannot edit your own permissions.")
        return redirect("/admin/staff/")
    perm_id = request.GET.get("permission", "")
    row = Permission.objects.filter(id=perm_id, a_id=a_id).first() if perm_id else \
        Permission.objects.filter(a_id=a_id).first()
    try:
        current = json.loads(row.permission) if row and row.permission else permission_schema()
    except ValueError:
        current = permission_schema()
    schema = permission_schema()
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "staff", "edit_permission", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        target_id = request.POST.get("permission_id", "") or (row.id if row else "")
        target = Permission.objects.filter(id=target_id, a_id=a_id).first()
        if not target:
            return render(request, "core/404.html", status=404)
        new = {"resources": {}, "pages": {}}
        for module, actions in schema["resources"].items():
            new["resources"][module] = {a: bool(request.POST.get(f"res_{module}_{a}")) for a in actions}
        for page in schema["pages"]:
            new["pages"][page] = bool(request.POST.get(f"page_{page}"))
        target.permission = json.dumps(new)
        target.status = request.POST.get("status", target.status)
        target.save()
        messages.success(request, "Permissions updated.")
        return redirect(f"/admin/staff/{a_id}/brands/")
    return render(request, "staff/staff_permissions.html",
                  {"member": member, "schema": schema, "current": current, "row": row,
                   "granted": _granted_keys(current)})


def _granted_keys(current: dict) -> set:
    granted = set()
    for module, actions in (current.get("resources") or {}).items():
        for action, on in (actions or {}).items():
            if on:
                granted.add(f"res_{module}_{action}")
    for page, on in (current.get("pages") or {}).items():
        if on:
            granted.add(f"page_{page}")
    return granted


@login_required
def reports(request):
    if not can_access_page(request.pp_permissions, "reports", request.pp_admin.role):
        return render(request, "staff/denied.html", status=403)
    preset, start, end = _resolve_range(request)
    if start and end and start > end:
        messages.error(request, "Invalid Date Range.")
        return redirect("/admin/reports/")
    brand = Brand.objects.filter(brand_id=getattr(request, "pp_brand_id", "")).first()
    rates = {}
    if brand:
        rates = {c.code: M.money_sanitize(str(c.rate)) for c in Currency.objects.filter(brand_id=brand.brand_id)}
    base_ccy = brand.currency_code if brand else "BDT"

    def to_base(amount, currency):
        amount = M.money_sanitize(str(amount))
        if not currency or currency == base_ccy:
            return amount
        if currency in rates and M.money_sanitize(rates[currency]) not in ("0", ""):
            return M.money_mul(amount, rates[currency])
        return amount

    qs = Transaction.objects.exclude(status="initiated")
    if getattr(request, "pp_brand_id", ""):
        qs = qs.filter(brand_id=request.pp_brand_id)
    if start:
        qs = qs.filter(created_date__gte=start)
    if end:
        qs = qs.filter(created_date__lte=end)
    completed = qs.filter(status="completed")
    revenue = "0"
    for row in completed.only("amount", "currency"):
        revenue = M.money_add(revenue, to_base(row.amount, row.currency))
    total_n = qs.count()
    done_n = completed.count()
    success_rate = round(done_n * 100 / total_n, 2) if total_n else 0
    average = M.money_div(revenue, str(done_n)) if done_n else "0"
    # Previous-period success rate + trend (PHP parity).
    prev_rate, trend = success_rate, "same"
    try:
        from datetime import datetime, timedelta
        if start and end:
            s = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
            e = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
            span = e - s
            ps = (s - span).strftime("%Y-%m-%d %H:%M:%S")
            pq = Transaction.objects.exclude(status="initiated")
            if getattr(request, "pp_brand_id", ""):
                pq = pq.filter(brand_id=request.pp_brand_id)
            pq = pq.filter(created_date__gte=ps, created_date__lt=s.strftime("%Y-%m-%d %H:%M:%S"))
            pt, pd = pq.count(), pq.filter(status="completed").count()
            prev_rate = round(pd * 100 / pt, 2) if pt else 0
            trend = "up" if success_rate > prev_rate else ("down" if success_rate < prev_rate else "same")
    except (ValueError, TypeError):
        pass
    by_status = list(qs.values("status").annotate(n=Count("id"), total=Sum("amount")))
    by_gateway = list(qs.values("gateway_id").annotate(n=Count("id"), total=Sum("amount")))
    return render(request, "staff/reports.html",
                  {"by_status": by_status, "by_gateway": by_gateway, "preset": preset,
                   "start": request.GET.get("start", ""), "end": request.GET.get("end", ""),
                   "revenue": M.money_round(revenue), "completed": done_n,
                   "success_rate": success_rate, "average": M.money_round(average),
                   "prev_success_rate": prev_rate, "success_trend": trend})


def _resolve_range(request):
    """Date presets mirroring dashboard/reports (today/yesterday/this_week/last_week/
    this_month/last_month/this_year/previous_year/custom). Returns (preset, start, end)."""
    from datetime import date, timedelta
    preset = request.GET.get("preset", "")
    today = date.today()
    if preset == "today":
        return preset, f"{today} 00:00:00", f"{today} 23:59:59"
    if preset == "yesterday":
        y = today - timedelta(days=1)
        return preset, f"{y} 00:00:00", f"{y} 23:59:59"
    if preset == "this_week":
        monday = today - timedelta(days=today.weekday())
        return preset, f"{monday} 00:00:00", f"{today} 23:59:59"
    if preset == "last_week":
        monday = today - timedelta(days=today.weekday() + 7)
        return preset, f"{monday} 00:00:00", f"{monday + timedelta(days=6)} 23:59:59"
    if preset == "this_month":
        return preset, f"{today.replace(day=1)} 00:00:00", f"{today} 23:59:59"
    if preset == "last_month":
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        last = today.replace(day=1) - timedelta(days=1)
        return preset, f"{first} 00:00:00", f"{last} 23:59:59"
    if preset == "this_year":
        return preset, f"{today.year}-01-01 00:00:00", f"{today} 23:59:59"
    if preset == "previous_year":
        return preset, f"{today.year - 1}-01-01 00:00:00", f"{today.year - 1}-12-31 23:59:59"
    start = request.GET.get("start", "").strip()
    end = request.GET.get("end", "").strip()
    s = f"{start} 00:00:00" if start else ""
    e = f"{end} 23:59:59" if end else ""
    return preset or "custom", s, e


@login_required
def activities(request):
    logs = BrowserLog.objects.all().order_by("-id")
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_until = request.GET.get("until", "").strip()
    q = request.GET.get("q", "").strip()
    if status in ("active", "expired"):
        logs = logs.filter(status=status)
    if q:
        logs = logs.filter(Q(ip__icontains=q) | Q(a_id__icontains=q) | Q(browser__icontains=q))
    if date_from:
        logs = logs.filter(created_date__gte=f"{date_from} 00:00:00")
    if date_until:
        logs = logs.filter(created_date__lte=f"{date_until} 23:59:59")
    hooks_list = WebhookLog.objects.all().order_by("-id")[:50]
    current_cookie = request.session.get("pp_cookie", "") or ""
    return render(request, "staff/activities.html",
                  {"page": _paginate(request, logs), "webhooks": hooks_list,
                   "status": status, "q": q, "date_from": date_from, "date_until": date_until,
                   "current_cookie": current_cookie})


@login_required
def my_account(request):
    admin = request.pp_admin
    if request.method == "POST":
        if "logout_others" in request.POST:
            # my-account-account-browser-sessions: two-step gate, then keep only this session.
            _gate_ok = (verify_totp(admin.twofa_secret, request.POST.get("twofa_code", ""), window=2)
                        if admin.twofa_status == "enable"
                        else check_password(request.POST.get("current_password", ""), admin.password))
            if not _gate_ok:
                messages.error(request, "The code/password you entered is incorrect. Please try again.")
                return redirect("/admin/my-account/")
            BrowserLog.objects.filter(a_id=admin.a_id).exclude(
                cookie=(request.session.get("pp_cookie", "") or "")[:40]).delete()
            messages.success(request, "Other browser sessions logged out.")
            return redirect("/admin/my-account/")
        username = request.POST.get("username", admin.username)
        email = request.POST.get("email", admin.email)
        full_name = request.POST.get("full_name", admin.full_name)
        if not full_name or not username or not email:
            messages.error(request, "Please fill in all required fields before proceeding.")
            return redirect("/admin/my-account/")
        try:
            from django.core.exceptions import ValidationError as _VE
            from django.core.validators import validate_email as _ve
            _ve(email)
        except _VE:
            messages.error(request, "Please enter a valid email address.")
            return redirect("/admin/my-account/")
        # Two-step gate (my-two-step-verify-code): profile writes require the
        # current password, or a valid TOTP code when 2FA is enabled.
        gate_ok = False
        if admin.twofa_status == "enable":
            gate_ok = verify_totp(admin.twofa_secret, request.POST.get("twofa_code", ""),
                                  window=2)
        else:
            gate_ok = check_password(request.POST.get("current_password", ""), admin.password)
        if not gate_ok:
            messages.error(request, "The code/password you entered is incorrect. Please try again.")
            return redirect("/admin/my-account/")
        if Admin.objects.filter(username=username).exclude(a_id=admin.a_id).exists():
            messages.error(request, "Username already exits.")
            return redirect("/admin/my-account/")
        if Admin.objects.filter(email=email).exclude(a_id=admin.a_id).exists():
            messages.error(request, "Email Address already exits.")
            return redirect("/admin/my-account/")
        admin.full_name = full_name
        admin.username = username
        admin.email = email
        if request.POST.get("password"):
            # PHP also rotates temp_password on password change.
            admin.password = make_password(request.POST["password"])
            admin.temp_password = make_password(M.generate_strong_password())
        # 2FA enable requires verifying a TOTP code (like PHP's QR + code step).
        if request.POST.get("twofa") == "enable" and admin.twofa_status != "enable":
            code = request.POST.get("twofa_code", "")
            secret = admin.twofa_secret
            if not secret or secret == "--":
                secret = generate_secret()
            if verify_totp(secret, code, window=2):
                admin.twofa_secret = secret
                admin.twofa_status = "enable"
            else:
                messages.error(request, "Invalid 2FA code. 2FA was not enabled.")
                return redirect("/admin/my-account/")
        elif request.POST.get("twofa") == "disable":
            code = request.POST.get("twofa_code", "")
            if admin.twofa_status == "enable" and not verify_totp(admin.twofa_secret, code, window=2):
                messages.error(request, "Invalid 2FA code.")
                return redirect("/admin/my-account/")
            admin.twofa_status = "disable"
        admin.save()
        messages.success(request, "Account updated.")
        return redirect("/admin/my-account/")
    sessions = BrowserLog.objects.filter(a_id=admin.a_id).order_by("-id")
    show_secret = admin.twofa_secret not in ("--", "", None)
    qr_url = otpauth_url(admin.twofa_secret, admin.email or admin.username) if show_secret else ""
    return render(request, "staff/my_account.html",
                  {"admin": admin, "qr_url": qr_url, "show_secret": show_secret,
                   "sessions": sessions, "current_cookie": (request.session.get("pp_cookie", "") or "")[:40]})


@login_required
def system_settings(request):
    from staff import system_ops
    section = request.GET.get("section", "general")
    if request.method == "POST":
        op = request.POST.get("op", "")
        if op == "cron-regenerate":
            if not has_permission(request.pp_permissions, "system_settings", "manage_cron",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            import secrets as _s
            set_env("cron-job", _s.token_hex(8))
            messages.success(request, "New cron key generated.")
            return redirect("/admin/system-settings/?section=cron")
        if op in ("update-setting", "update-check", "update-download", "update-install"):
            if not has_permission(request.pp_permissions, "system_settings", "manage_update",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            if op == "update-setting":
                set_env("system-settings-update_channel",
                        request.POST.get("update_channel", "stable"))
                set_env("system-settings-automatic_update",
                        request.POST.get("automatic_update", ""))
                set_env("system-settings-create_backup",
                        request.POST.get("create_backup", ""))
                messages.success(request, "Update settings saved.")
            elif op == "update-check":
                result = system_ops.update_check()
                messages.info(request, f"{result.get('title')}: {result.get('message')}")
            elif op == "update-download":
                result = system_ops.update_download()
                (messages.success if result.get("status") == "true" else messages.error)(
                    request, f"{result.get('title')}: {result.get('message')}")
            elif op == "update-install":
                result = system_ops.update_install()
                (messages.success if result.get("status") == "true" else messages.error)(
                    request, f"{result.get('title')}: {result.get('message')}")
            return redirect("/admin/system-settings/?section=update")
        if op == "import":
            if not has_permission(request.pp_permissions, "system_settings", "manage_import",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            result = system_ops.import_module(request.FILES.get("zip_file") or request.FILES.get("module_zip"))
            (messages.success if result.get("status") == "true" else messages.error)(
                request, f"{result.get('title')}: {result.get('message')}")
            return redirect("/admin/system-settings/?section=import")
        if not has_permission(request.pp_permissions, "system_settings", "manage_general", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        for k, v in request.POST.items():
            if k.startswith("env_"):
                set_env(k[4:], v)
        messages.success(request, "Settings saved.")
        return redirect(f"/admin/system-settings/?section={section}")
    from django.conf import settings as _s
    ctx = {"section": section,
           "cron_token": get_env("cron-job"),
           "cron_command": f"curl -s {request.build_absolute_uri('/').rstrip('/')}/cron/{get_env('cron-job')} >/dev/null 2>&1",
           "webhook_limit": get_env("geneal-application-settings-webhook_attempts_limit"),
           "auto_update": get_env("system-settings-automatic_update"),
           "update_channel": get_env("system-settings-update_channel"),
           "create_backup": get_env("system-settings-create_backup"),
           "current_version": _s.PIPRAPAY_VERSION,
           "last_check": get_env("last-auto-update-check"),
           "last_version": get_env("last-update-version"),
           "last_version_name": get_env("last-update-version-name")}
    return render(request, "staff/system_settings.html", ctx)


@login_required
@page_required("addons")
def addon_list(request):
    from core.models import Addon
    qs = Addon.objects.all().order_by("-id")
    status = request.GET.get("status", "").strip()
    aq = request.GET.get("q", "").strip()
    if status in ("active", "inactive"):
        qs = qs.filter(status=status)
    if aq:
        qs = qs.filter(Q(name__icontains=aq) | Q(slug__icontains=aq))
    if request.method == "POST":
        from staff import system_ops
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "addons", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            from core.models import AddonParameter
            AddonParameter.objects.filter(addon_id=request.POST["delete"]).delete()
            Addon.objects.filter(addon_id=request.POST["delete"]).delete()
            return redirect("/admin/addons/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "addons", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                from core.models import AddonParameter as _AP
                _AP.objects.filter(addon_id__in=ids).delete()
                Addon.objects.filter(addon_id__in=ids).delete()
            elif action in ("activated", "inactivated"):
                if not has_permission(request.pp_permissions, "addons", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Addon.objects.filter(addon_id__in=ids).update(
                    status="active" if action == "activated" else "inactive", updated_date=timezone.now())
            return redirect("/admin/addons/")
        if "upload" in request.POST:
            if not has_permission(request.pp_permissions, "addons", "create", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            if request.POST.get("slug"):
                # addons-create by slug: register an already-present addon dir.
                from pathlib import Path as _P
                from django.conf import settings as _s
                slug = request.POST["slug"].strip()
                target = _P(_s.BASE_DIR) / "pp-modules" / "pp-addons" / slug
                if not (target / "class.php").exists() and not target.exists():
                    messages.error(request, "Addon files not found for this slug.")
                else:
                    from core.models import Addon as _A
                    _A.objects.update_or_create(
                        slug=slug, defaults={"addon_id": M.generate_item_id(),
                                             "name": request.POST.get("name", "") or slug,
                                             "status": "active"})
                    messages.success(request, f"Addon '{slug}' registered.")
            else:
                result = system_ops.install_addon(request.FILES.get("addon_zip"),
                                                  name=request.POST.get("name", ""))
                (messages.success if result.get("status") == "true" else messages.error)(
                    request, f"{result.get('title')}: {result.get('message')}")
            return redirect("/admin/addons/")
    return render(request, "staff/addon_list.html", {"page": _paginate(request, qs), "status": status,
                                                        "q": aq})


@login_required
@page_required("addons")
def addon_edit(request, addon_id: str):
    from core.models import Addon, AddonParameter
    addon = Addon.objects.filter(addon_id=addon_id).first()
    if not addon:
        return render(request, "core/404.html", status=404)
    params = list(AddonParameter.objects.filter(addon_id=addon_id))
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "addons", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        addon.status = request.POST.get("status", addon.status)
        addon.save()
        skip = {"csrf_token", "action", "addon-id", "status"}
        for key in request.POST:
            if key in skip:
                continue
            name = key[4:] if key.startswith("opt_") else key
            if name in request.FILES:
                from core.storage import upload_image
                up = upload_image(request.FILES[name], 5 * 1024 * 1024)
                if up.get("status"):
                    value = f"/media/storage/{up['file']}"
                else:
                    continue
            else:
                value = request.POST.get(key, "")
            AddonParameter.objects.update_or_create(addon_id=addon_id, option_name=name,
                                                    defaults={"value": value})
        messages.success(request, "Addon saved.")
        return redirect("/admin/addons/")
    return render(request, "staff/addon_edit.html", {"addon": addon, "params": params})


@login_required
def logout_view(request):
    request.session.flush()
    return redirect("/login/")
