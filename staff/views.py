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
from django.contrib.auth.hashers import make_password
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
    """Mirror dashboard-transaction-statistics: totals per day over a preset range (JSON)."""
    from django.http import JsonResponse
    preset, start, end = _resolve_range(request)
    qs = Transaction.objects.exclude(status="initiated")
    brand_id = request.GET.get("brand") or request.session.get("pp_brand_id", "")
    if brand_id:
        qs = qs.filter(brand_id=brand_id)
    if start:
        qs = qs.filter(created_date__gte=start)
    if end:
        qs = qs.filter(created_date__lte=end)
    return JsonResponse({
        "total": qs.count(),
        "completed": qs.filter(status="completed").count(),
        "pending": qs.filter(status="pending").count(),
        "volume": M.money_round(str(qs.filter(status="completed").aggregate(s=Sum("amount"))["s"] or 0)),
    })


@login_required
def dashboard_gateway_stats(request):
    """Mirror dashboard-gateway-statistics: per-gateway counts (JSON)."""
    from django.http import JsonResponse
    qs = Transaction.objects.exclude(status="initiated")
    brand_id = request.GET.get("brand") or request.session.get("pp_brand_id", "")
    if brand_id:
        qs = qs.filter(brand_id=brand_id)
    return JsonResponse({"gateways": list(
        qs.values("gateway_id").annotate(n=Count("id"), total=Sum("amount")).order_by("-n"))})


@login_required
@page_required("transaction")
def transaction_list(request):
    qs = Transaction.objects.exclude(status="initiated").order_by("-id")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_until = request.GET.get("until", "").strip()
    if q:
        qs = qs.filter(Q(ref__icontains=q) | Q(trx_id__icontains=q) | Q(sender__icontains=q)
                       | Q(customer_info__icontains=q))
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
            for txn in Transaction.objects.filter(id__in=ids):
                if action == "deleted":
                    txn.delete()
                elif action == "approved":
                    txn.status = "completed"
                    txn.save()
                elif action == "canceled":
                    txn.status = "canceled"
                    txn.save()
                elif action == "refunded":
                    _refund_transaction(txn)
                elif action == "ipnsend":
                    _send_transaction_ipn(txn)
            messages.success(request, "Bulk action completed.")
            return redirect("/admin/transaction/")
    tabs = {s: Transaction.objects.exclude(status="initiated").filter(status=s).count()
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
        for label, key in (("Refund TrxID", "refundTrxId"), ("Refund Amount", "refundAmount"),
                           ("Refund Time", "completedTime")):
            val = (result.get("data") or {}).get(key, "")
            if val:
                info.append({"label": label, "value": val})
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
    qs = Customer.objects.all().order_by("-id")
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
                    status="active" if action == "activated" else "suspend")
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
                c.name = request.POST.get("name", c.name)
                c.email = request.POST.get("email", c.email)
                c.mobile = request.POST.get("mobile", c.mobile)
                c.status = request.POST.get("status", c.status)
                c.suspend_reason = request.POST.get("suspend_reason", c.suspend_reason or "")
                c.save()
            return redirect("/admin/customers/")
        # create
        if not has_permission(request.pp_permissions, "customers", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        Customer.objects.create(ref=M.generate_item_id(), brand_id=request.POST.get("brand_id", ""),
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
    qs = Invoice.objects.all().order_by("-id")
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
        customer_info = request.POST.get("customer_info", "")
        if not customer_info:
            customer_info = json.dumps({"name": request.POST.get("customer_name", ""),
                                        "email": request.POST.get("customer_email", ""),
                                        "mobile": request.POST.get("customer_mobile", "")})
        data = {
            "brand_id": request.POST.get("brand_id", ""), "customer_info": customer_info or "{}",
            "gateway_id": request.POST.get("gateway_id", "--"), "currency": request.POST.get("currency", "BDT"),
            "due_date": due_date, "shipping": M.money_sanitize(request.POST.get("shipping", "0")),
            "status": request.POST.get("status", "unpaid"), "note": request.POST.get("note", ""),
            "private_note": request.POST.get("private_note", ""),
        }
        if inv:
            for k, v in data.items():
                setattr(inv, k, v)
            inv.save()
        else:
            inv = Invoice.objects.create(ref=request.POST.get("ref") or M.generate_item_id(27, 27), **data)
        # items rewrite (deleted_items semantics = full rewrite, like PHP).
        InvoiceItem.objects.filter(invoice_id=inv.ref).delete()
        descs = request.POST.getlist("item_description")
        for i, d in enumerate(descs):
            def _g(name, default="0"):
                vals = request.POST.getlist(name)
                return vals[i] if i < len(vals) else default
            try:
                qty = int(float(_g("item_quantity", "1") or 1))
            except (ValueError, TypeError):
                qty = 1
            InvoiceItem.objects.create(brand_id=inv.brand_id, invoice_id=inv.ref, description=d,
                                       amount=M.money_sanitize(_g("item_amount")),
                                       quantity=qty,
                                       discount=M.money_sanitize(_g("item_discount")),
                                       vat=M.money_sanitize(_g("item_vat")))
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
    qs = PaymentLink.objects.all().order_by("-id")
    status = request.GET.get("status", "").strip()
    if status in ("active", "inactive"):
        qs = qs.filter(status=status)
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "payment_link", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
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
        if "bulk" in request.POST and request.POST.get("bulk") == "deleted":
            if not has_permission(request.pp_permissions, "payment_link", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            PaymentLink.objects.filter(ref__in=request.POST.getlist("selected_ids")).delete()
            return redirect("/admin/payment-link/")
        if "default_currency" in request.POST:
            from core.env import set_env
            set_env("payment-link-default-currency", request.POST.get("currency", ""),
                    request.POST.get("brand_id", "both"))
            messages.success(request, "Default link currency saved.")
            return redirect("/admin/payment-link/")
    from core.env import get_env
    return render(request, "staff/payment_link_list.html",
                  {"page": _paginate(request, qs), "status": status, "brands": Brand.objects.all(),
                   "get_env": get_env})


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
                formType=form_type, fieldName=field_name, required=required, value=value)
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
        product_info = request.POST.get("product_info", "")
        if title or description:
            product_info = json.dumps({"title": title, "description": description})
        def _qty(v):
            try:
                return int(float(M.money_sanitize(v or "0")))
            except (ValueError, TypeError):
                return 0

        data = {
            "brand_id": request.POST.get("brand_id", ""),
            "product_info": product_info or "{}",
            "amount": M.money_sanitize(request.POST.get("amount", "0")),
            "quantity": _qty(request.POST.get("quantity", "0")),
            "currency": request.POST.get("currency", "BDT"),
            "expired_date": expiry or "--",
            "status": request.POST.get("status", "active"),
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
    qs = Gateway.objects.all().order_by("-id")
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
                    status="active" if action == "activated" else "inactive")
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
            gid = M.generate_item_id()
            info = plugin.info() if plugin else {}
            color = plugin.color() if plugin else {}
            gw = Gateway.objects.create(
                gateway_id=gid, brand_id=request.POST.get("brand_id", ""), slug=slug or "bank",
                name=request.POST.get("name", info.get("title", slug or "Bank")),
                display=request.POST.get("display", info.get("title", slug or "Bank")),
                currency=request.POST.get("currency", info.get("currency", "BDT")),
                tab=request.POST.get("tab", info.get("tab", "bank" if is_bank_form else "mfs")),
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
    qs = SmsData.objects.all().order_by("-id")
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_until = request.GET.get("until", "").strip()
    q = request.GET.get("q", "").strip()
    if status in ("approved", "awaiting-review", "used", "error"):
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(sender__icontains=q) | Q(device_id__icontains=q) | Q(trx_id__icontains=q))
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
                SmsData.objects.filter(id__in=ids).update(status=action)
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
                  {"page": _paginate(request, qs), "status": status, "q": q,
                   "date_from": date_from, "date_until": date_until,
                   "providers": sender_whitelist(),
                   "devices": Device.objects.filter(status="used").order_by("-id")})


@login_required
@page_required("device")
def devices(request):
    qs = Device.objects.all().order_by("-id")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "device", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Device.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/devices/")
        if "bulk" in request.POST and request.POST.get("bulk") == "deleted":
            if not has_permission(request.pp_permissions, "device", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Device.objects.filter(id__in=request.POST.getlist("selected_ids")).delete()
            return redirect("/admin/devices/")
    from django.conf import settings as _s
    base_url = _s.ALLOWED_HOSTS[0] if _s.ALLOWED_HOSTS and _s.ALLOWED_HOSTS[0] != "*" else request.get_host()
    return render(request, "staff/devices.html", {"page": _paginate(request, qs), "base_url": base_url})


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
                    status="active" if action == "activated" else "inactive")
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        if "edit_id" in request.POST:
            row = BalanceVerification.objects.filter(id=request.POST["edit_id"]).first()
            if row:
                row.sender_key = request.POST.get("sender_key", row.sender_key)
                row.type = request.POST.get("type", row.type)
                row.current_balance = M.money_sanitize(request.POST.get("current_balance", row.current_balance))
                row.simslot = request.POST.get("simslot", row.simslot)
                row.status = request.POST.get("status", row.status)
                row.save()
            return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
        # create: requires used parent device + no duplicate (device+sender+type)
        device_id = request.POST.get("device_id", d_id or "")
        sender_key = request.POST.get("sender_key", "")
        btype = request.POST.get("type", "Personal")
        if not device_id or not sender_key:
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
            current_balance=M.money_sanitize(request.POST.get("current_balance", "0")),
            simslot=request.POST.get("simslot", ""), status=request.POST.get("status", "active"))
        messages.success(request, "Balance verification rule created.")
        return redirect(f"/admin/devices/balance-verification/?d_id={d_id}")
    providers = {k: v for k, v in sender_whitelist().items() if v.get("balance_verify") == "true"}
    return render(request, "staff/balance_verification.html",
                  {"page": _paginate(request, qs), "d_id": d_id, "device": device, "providers": providers})


@login_required
@page_required("domains")
def domains(request):
    qs = Domain.objects.all().order_by("-id")
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
            elif action in ("activated", "inactivated"):
                if not has_permission(request.pp_permissions, "domains", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Domain.objects.filter(id__in=ids).update(
                    status="active" if action == "activated" else "inactive")
            return redirect("/admin/domains/")
        if "edit_id" in request.POST:
            if not has_permission(request.pp_permissions, "domains", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            row = Domain.objects.filter(id=request.POST["edit_id"]).first()
            if row:
                row.domain = request.POST.get("domain", row.domain)
                row.status = request.POST.get("status", row.status)
                row.save()
            return redirect("/admin/domains/")
        if not has_permission(request.pp_permissions, "domains", "whitelist", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        Domain.objects.create(domain=request.POST.get("domain", ""), status=request.POST.get("status", "active"))
        return redirect("/admin/domains/")
    return render(request, "staff/domains.html", {"page": _paginate(request, qs)})


@login_required
@page_required("brands")
def brand_list(request):
    qs = Brand.objects.all().order_by("-id")
    if request.method == "POST" and "delete" in request.POST:
        if not has_permission(request.pp_permissions, "brands", "delete", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        Brand.objects.filter(brand_id=request.POST["delete"]).delete()
        return redirect("/admin/brands/")
    return render(request, "staff/brand_list.html", {"page": _paginate(request, qs)})


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
            # edit-brand is rename-only; the default brand row cannot be renamed.
            first = Brand.objects.order_by("id").first()
            if first and brand.id == first.id and request.POST.get("identify_name", brand.identify_name) != brand.identify_name:
                messages.error(request, "The default brand cannot be renamed.")
                return redirect("/admin/brands/")
            brand.identify_name = request.POST.get("identify_name", brand.identify_name)
            brand.save()
        else:
            # create-new-brand: single name + auto-seed permission + currency rows.
            name = (request.POST.get("identify_name") or "").strip()
            if not name:
                messages.error(request, "Please fill in all required fields before proceeding.")
                return redirect("/admin/brands/")
            new_id = M.generate_item_id()
            brand = Brand.objects.create(brand_id=new_id, identify_name=name)
            Permission.objects.create(brand_id=new_id, a_id=request.pp_admin.a_id,
                                      permission=json.dumps(permission_schema()))
            Currency.objects.get_or_create(brand_id=new_id, code="BDT",
                                           defaults={"symbol": "৳", "rate": "0"})
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
    brand = Brand.objects.first()
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "brand_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        if brand:
            for f in BRAND_GENERAL_FIELDS:
                if f in request.POST:
                    setattr(brand, f, request.POST[f])
            for upload_field in ("favicon", "logo"):
                if request.FILES.get(upload_field):
                    from core.storage import upload_image
                    up = upload_image(request.FILES[upload_field], 2 * 1024 * 1024)
                    if up.get("status"):
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
    keys = ApiKey.objects.all().order_by("-id")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "api_settings", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            ApiKey.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/brand-setting/api/")
        if "bulk" in request.POST and request.POST.get("bulk") == "deleted":
            if not has_permission(request.pp_permissions, "api_settings", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            ApiKey.objects.filter(id__in=request.POST.getlist("selected_ids")).delete()
            return redirect("/admin/brand-setting/api/")
        if "edit_id" in request.POST:
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
                key.api_scopes = json.dumps(request.POST.getlist("scopes") or ["create_payment"])
                key.save()
            return redirect("/admin/brand-setting/api/")
        if not has_permission(request.pp_permissions, "api_settings", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        import secrets as _s
        brand_id = request.POST.get("brand_id", "")
        name = request.POST.get("name", "")
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
                              api_scopes=json.dumps(request.POST.getlist("scopes") or ["create_payment", "verify_payment"]),
                              status=request.POST.get("status", "active"))
        messages.success(request, "API key created.")
        return redirect("/admin/brand-setting/api/")
    return render(request, "staff/brand_api.html", {"keys": keys, "brands": Brand.objects.all()})


@login_required
@page_required("brand_settings")
def brand_currency(request):
    qs = Currency.objects.all().order_by("brand_id", "code")
    if request.method == "POST":
        if "sync" in request.POST:
            # currency-rateSync: refresh one row from the FX source.
            if not has_permission(request.pp_permissions, "currency_settings", "sync_rate",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            _sync_currency_rate(request.POST["sync"])
            messages.success(request, "The selected currency rate have been updated successfully.")
            return redirect("/admin/brand-setting/currency/")
        if "bulk_sync" in request.POST:
            if not has_permission(request.pp_permissions, "currency_settings", "sync_rate",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            for cid in request.POST.getlist("selected_ids"):
                _sync_currency_rate(cid)
            messages.success(request, "Currency rates updated.")
            return redirect("/admin/brand-setting/currency/")
        if "bulk_import" in request.POST:
            if not has_permission(request.pp_permissions, "currency_settings", "import",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            _import_default_currencies(request.POST.get("brand_id", ""))
            messages.success(request, "Default currencies imported.")
            return redirect("/admin/brand-setting/currency/")
        if not has_permission(request.pp_permissions, "currency_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        if "edit_id" in request.POST:
            row = Currency.objects.filter(id=request.POST["edit_id"]).first()
            if row:
                row.symbol = request.POST.get("symbol", row.symbol)
                row.rate = M.money_sanitize(request.POST.get("rate", row.rate))
                row.save()
        else:
            Currency.objects.update_or_create(brand_id=request.POST.get("brand_id", ""), code=request.POST.get("code", ""),
                                              defaults={"symbol": request.POST.get("symbol", ""),
                                                        "rate": M.money_sanitize(request.POST.get("rate", "0"))})
        return redirect("/admin/brand-setting/currency/")
    return render(request, "staff/brand_currency.html", {"currencies": qs, "brands": Brand.objects.all()})


def _sync_currency_rate(currency_pk) -> None:
    """Mirror currency-rateSync: rate = 1/source-rate for the brand base currency."""
    import requests
    row = Currency.objects.filter(id=currency_pk).first()
    if not row:
        return
    brand = Brand.objects.filter(brand_id=row.brand_id).first()
    base = (brand.currency_code if brand else "BDT").lower()
    try:
        data = requests.get(
            f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.json",
            timeout=10, verify=False).json()
    except Exception:
        return
    rates = data.get(base, {})
    if not isinstance(rates, dict):
        return
    rate = rates.get(row.code.lower(), 0)
    if not rate or rate <= 0:
        return
    row.rate = M.money_div("1", M.money_sanitize(f"{rate:.14f}"))
    row.save(update_fields=["rate", "updated_date"])


def _import_default_currencies(brand_id: str) -> None:
    """Mirror currency-bulkImport: seed common currencies for a brand."""
    defaults = [("BDT", "৳"), ("USD", "$"), ("EUR", "€"), ("GBP", "£"), ("INR", "₹"),
                ("PKR", "₨"), ("AED", "د.إ"), ("SAR", "﷼"), ("MYR", "RM"), ("SGD", "S$")]
    for code, symbol in defaults:
        Currency.objects.get_or_create(brand_id=brand_id, code=code,
                                       defaults={"symbol": symbol, "rate": "0"})


@login_required
@page_required("brand_settings")
def brand_faq(request):
    qs = Faq.objects.all().order_by("-id")
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
                    status="active" if action == "activated" else "inactive")
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
        Faq.objects.create(brand_id=request.POST.get("brand_id", ""), title=request.POST.get("title", ""),
                           description=request.POST.get("description", ""),
                           status=request.POST.get("status", "active"))
        return redirect("/admin/brand-setting/faq/")
    return render(request, "staff/brand_faq.html", {"faqs": qs, "brands": Brand.objects.all()})


@login_required
@page_required("brand_settings")
def brand_themes(request):
    from themes.twenty_six import THEMES, TwentySixTheme
    gallery = {slug: cls().info() for slug, cls in THEMES.items()}
    active_slug = (Brand.objects.first().theme if Brand.objects.first() else "twenty-six") or "twenty-six"
    theme = THEMES.get(active_slug, TwentySixTheme)()
    if request.method == "POST":
        if "activate" in request.POST:
            # themes-new-active: UPDATE brands SET theme=slug.
            if not has_permission(request.pp_permissions, "theme_settings", "edit", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            slug = request.POST["activate"]
            if slug in THEMES:
                Brand.objects.all().update(theme=slug)
                messages.success(request, "Theme activated.")
            return redirect("/admin/brand-setting/themes/")
        if not has_permission(request.pp_permissions, "theme_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        brand_id = request.POST.get("brand_id", "both")
        for field in theme.fields():
            if field.get("type") == "image" and request.FILES.get(field["name"]):
                from core.storage import upload_image
                up = upload_image(request.FILES[field["name"]], 5 * 1024 * 1024)
                if up.get("status"):
                    set_env(f"theme-{field['name']}", f"/media/storage/{up['file']}", brand_id)
            elif field["name"] in request.POST:
                set_env(f"theme-{field['name']}", request.POST[field["name"]], brand_id)
        messages.success(request, "Theme settings saved.")
        return redirect("/admin/brand-setting/themes/")
    saved = {f["name"]: get_env(f"theme-{f['name']}") for f in theme.fields()}
    return render(request, "staff/brand_themes.html",
                  {"fields": theme.fields(), "saved": saved, "brands": Brand.objects.all(),
                   "gallery": gallery, "active_slug": active_slug})


@login_required
@page_required("staff_management")
def staff_list(request):
    qs = Admin.objects.all().order_by("-id")
    status = request.GET.get("status", "").strip()
    q = request.GET.get("q", "").strip()
    if status in ("active", "suspend"):
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(username__icontains=q) | Q(email__icontains=q))
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "staff", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Admin.objects.filter(a_id=request.POST["delete"]).exclude(role="admin").delete()
            return redirect("/admin/staff/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = [i for i in request.POST.getlist("selected_ids") if i != request.pp_admin.a_id]
            if action == "deleted":
                if not has_permission(request.pp_permissions, "staff", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Admin.objects.filter(a_id__in=ids).exclude(role="admin").delete()
            elif action in ("activated", "suspended"):
                if not has_permission(request.pp_permissions, "staff", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Admin.objects.filter(a_id__in=ids).update(
                    status="active" if action == "activated" else "suspend")
            return redirect("/admin/staff/")
    return render(request, "staff/staff_list.html",
                  {"page": _paginate(request, qs), "status": status, "q": q})


@login_required
@page_required("staff_management")
def staff_edit(request, a_id: str = ""):
    member = Admin.objects.filter(a_id=a_id, role="staff").first() if a_id else None
    if a_id and not member:
        return render(request, "core/404.html", status=404)
    if member and member.a_id == request.pp_admin.a_id:
        messages.error(request, "You cannot edit your own staff record here. Use My Account.")
        return redirect("/admin/staff/")
    brands = Brand.objects.all()
    assigned = []
    if member:
        assigned = [p.brand_id for p in Permission.objects.filter(a_id=a_id)]
    if request.method == "POST":
        need = "edit" if a_id else "create"
        if not has_permission(request.pp_permissions, "staff", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        if member:
            username = request.POST.get("username", member.username)
            email = request.POST.get("email", member.email)
            if Admin.objects.filter(username=username).exclude(a_id=member.a_id).exists():
                messages.error(request, "Username already exists.")
                return redirect(f"/admin/staff/{a_id}/")
            if Admin.objects.filter(email=email).exclude(a_id=member.a_id).exists():
                messages.error(request, "Email already exists.")
                return redirect(f"/admin/staff/{a_id}/")
            member.full_name = request.POST.get("full_name", member.full_name)
            member.username = username
            member.email = email
            member.status = request.POST.get("status", member.status)
            if request.POST.get("password"):
                member.password = make_password(request.POST["password"])
                member.temp_password = make_password(M.generate_strong_password())
            member.save()
        else:
            username, email = request.POST.get("username", ""), request.POST.get("email", "")
            if not username or not email or not request.POST.get("full_name"):
                messages.error(request, "Please fill in all required fields before proceeding.")
                return redirect("/admin/staff/create/")
            if Admin.objects.filter(username=username).exists():
                messages.error(request, "Username already exists.")
                return redirect("/admin/staff/create/")
            if Admin.objects.filter(email=email).exists():
                messages.error(request, "Email already exists.")
                return redirect("/admin/staff/create/")
            member = Admin.objects.create(a_id=M.generate_item_id(), full_name=request.POST.get("full_name", ""),
                                          username=username, email=email,
                                          password=make_password(request.POST.get("password", M.generate_strong_password())),
                                          temp_password=make_password(M.generate_strong_password()),
                                          role="staff", status=request.POST.get("status", "active"))
        # brand assignment (assign_brand_to)
        Permission.objects.filter(a_id=member.a_id).delete()
        for b in request.POST.getlist("brands"):
            Permission.objects.create(brand_id=b, a_id=member.a_id, permission=json.dumps(permission_schema()))
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
    rows = Permission.objects.filter(a_id=a_id).order_by("id")
    if request.method == "POST":
        if "add_brand" in request.POST:
            if not has_permission(request.pp_permissions, "staff", "assign_brand_to", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            brand_id = request.POST.get("brand_id", "")
            if brand_id and not rows.filter(brand_id=brand_id).exists():
                Permission.objects.create(brand_id=brand_id, a_id=a_id,
                                          permission=json.dumps(permission_schema()))
            return redirect(f"/admin/staff/{a_id}/brands/")
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "staff", "delete_permission_of",
                                  request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            rows.filter(id=request.POST["delete"]).delete()
            return redirect(f"/admin/staff/{a_id}/brands/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "staff", "delete_permission_of",
                                      request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                rows.filter(id__in=ids).delete()
            elif action in ("activated", "suspended"):
                if not has_permission(request.pp_permissions, "staff", "edit_permission",
                                      request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                rows.filter(id__in=ids).update(
                    status="active" if action == "activated" else "suspend")
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
    qs = Transaction.objects.exclude(status="initiated")
    if start:
        qs = qs.filter(created_date__gte=start)
    if end:
        qs = qs.filter(created_date__lte=end)
    completed = qs.filter(status="completed")
    revenue = completed.aggregate(s=Sum("amount"))["s"] or 0
    total_n = qs.count()
    done_n = completed.count()
    success_rate = round(done_n * 100 / total_n, 2) if total_n else 0
    average = (revenue / done_n) if done_n else 0
    by_status = list(qs.values("status").annotate(n=Count("id"), total=Sum("amount")))
    by_gateway = list(qs.values("gateway_id").annotate(n=Count("id"), total=Sum("amount")))
    return render(request, "staff/reports.html",
                  {"by_status": by_status, "by_gateway": by_gateway, "preset": preset,
                   "start": request.GET.get("start", ""), "end": request.GET.get("end", ""),
                   "revenue": M.money_round(str(revenue)), "completed": done_n,
                   "success_rate": success_rate, "average": M.money_round(str(average))})


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
            # my-account-account-browser-sessions: keep only this session.
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
        if "@" not in email:
            messages.error(request, "Please enter a valid email address.")
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
            set_env("cron-job", M.generate_item_id())
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
            result = system_ops.import_module(request.FILES.get("module_zip"))
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
    if status in ("active", "inactive"):
        qs = qs.filter(status=status)
    if request.method == "POST":
        from staff import system_ops
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "addons", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Addon.objects.filter(addon_id=request.POST["delete"]).delete()
            return redirect("/admin/addons/")
        if "bulk" in request.POST:
            action = request.POST.get("bulk")
            ids = request.POST.getlist("selected_ids")
            if action == "deleted":
                if not has_permission(request.pp_permissions, "addons", "delete", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Addon.objects.filter(addon_id__in=ids).delete()
            elif action in ("activated", "inactivated"):
                if not has_permission(request.pp_permissions, "addons", "edit", request.pp_admin.role):
                    return render(request, "staff/denied.html", status=403)
                Addon.objects.filter(addon_id__in=ids).update(
                    status="active" if action == "activated" else "inactive")
            return redirect("/admin/addons/")
        if "upload" in request.POST:
            if not has_permission(request.pp_permissions, "addons", "create", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            result = system_ops.install_addon(request.FILES.get("addon_zip"),
                                              name=request.POST.get("name", ""))
            (messages.success if result.get("status") == "true" else messages.error)(
                request, f"{result.get('title')}: {result.get('message')}")
            return redirect("/admin/addons/")
    return render(request, "staff/addon_list.html", {"page": _paginate(request, qs), "status": status})


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
        for key in [k for k in request.POST if k.startswith("opt_")]:
            name = key[4:]
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
