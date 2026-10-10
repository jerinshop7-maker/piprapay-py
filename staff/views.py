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
from core.totp import generate_secret, otpauth_url
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
    ctx = {
        "total_transactions": txns.count(),
        "completed": txns.filter(status="completed").count(),
        "pending": txns.filter(status="pending").count(),
        "volume_30d": M.money_round(str(volume)),
        "recent": txns.order_by("-id")[:10],
        "brands": Brand.objects.all(),
        "brand_id": brand_id,
    }
    return render(request, "staff/dashboard.html", ctx)


@login_required
@page_required("transaction")
def transaction_list(request):
    qs = Transaction.objects.all().order_by("-id")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(ref__icontains=q) | Q(trx_id__icontains=q) | Q(sender__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if request.method == "POST" and "delete" in request.POST:
        if not has_permission(request.pp_permissions, "transaction", "delete", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        Transaction.objects.filter(id=request.POST.get("delete")).delete()
        messages.success(request, "Transaction deleted.")
        return redirect("/admin/transaction/")
    return render(request, "staff/transaction_list.html", {"page": _paginate(request, qs), "q": q, "status": status})


@login_required
@page_required("transaction")
def transaction_edit(request, pk: int):
    txn = Transaction.objects.filter(id=pk).first()
    if not txn:
        return render(request, "core/404.html", status=404)
    if request.method == "POST":
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
                txn.status = "refunded"
                txn.save()
            elif action == "send_ipn":
                if txn.webhook_url and txn.webhook_url != "--":
                    from core.transactions import transaction_payload
                    code = send_ipn_multi([{"id": txn.id, "url": txn.webhook_url,
                                            "payload": transaction_payload(txn)}]).get(txn.id, 0)
                    messages.info(request, f"Webhook delivery HTTP {code}.")
                return redirect(f"/admin/transaction/{pk}/")
            messages.success(request, f"Transaction {action}d.")
            return redirect("/admin/transaction/")
    return render(request, "staff/transaction_edit.html", {"txn": txn})


@login_required
@page_required("customers")
def customers(request):
    qs = Customer.objects.all().order_by("-id")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(mobile__icontains=q))
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "customers", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            Customer.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/customers/")
        if "suspend" in request.POST:
            c = Customer.objects.filter(id=request.POST["suspend"]).first()
            if c:
                c.status = "suspend" if c.status == "active" else "active"
                c.suspend_reason = request.POST.get("reason", c.suspend_reason or "")
                c.save()
                return redirect("/admin/customers/")
        # create
        if not has_permission(request.pp_permissions, "customers", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        Customer.objects.create(ref=M.generate_item_id(), brand_id=request.POST.get("brand_id", ""),
                                name=request.POST.get("name", ""), email=request.POST.get("email", ""),
                                mobile=request.POST.get("mobile", ""))
        return redirect("/admin/customers/")
    return render(request, "staff/customers.html", {"page": _paginate(request, qs), "q": q, "brands": Brand.objects.all()})


@login_required
@page_required("invoice")
def invoice_list(request):
    qs = Invoice.objects.all().order_by("-id")
    if request.method == "POST" and "delete" in request.POST:
        if not has_permission(request.pp_permissions, "invoice", "delete", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        ref = request.POST["delete"]
        InvoiceItem.objects.filter(invoice_id=ref).delete()
        Invoice.objects.filter(ref=ref).delete()
        return redirect("/admin/invoice/")
    return render(request, "staff/invoice_list.html", {"page": _paginate(request, qs)})


@login_required
@page_required("invoice")
def invoice_edit(request, ref: str = ""):
    inv = Invoice.objects.filter(ref=ref).first() if ref else None
    items = list(InvoiceItem.objects.filter(invoice_id=ref)) if ref else []
    if request.method == "POST":
        need = "edit" if ref else "create"
        if not has_permission(request.pp_permissions, "invoice", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        data = {
            "brand_id": request.POST.get("brand_id", ""), "customer_info": request.POST.get("customer_info", "{}"),
            "gateway_id": request.POST.get("gateway_id", "--"), "currency": request.POST.get("currency", "BDT"),
            "due_date": request.POST.get("due_date", ""), "shipping": request.POST.get("shipping", "0"),
            "status": request.POST.get("status", "unpaid"), "note": request.POST.get("note", ""),
            "private_note": request.POST.get("private_note", ""),
        }
        if inv:
            for k, v in data.items():
                setattr(inv, k, v)
            inv.save()
        else:
            inv = Invoice.objects.create(ref=request.POST.get("ref") or M.generate_item_id(12, 12), **data)
        # items: parallel lists description/amount/quantity/discount/vat
        InvoiceItem.objects.filter(invoice_id=inv.ref).delete()
        descs = request.POST.getlist("item_description")
        for i, d in enumerate(descs):
            def _g(name, default="0"):
                vals = request.POST.getlist(name)
                return vals[i] if i < len(vals) else default
            InvoiceItem.objects.create(brand_id=inv.brand_id, invoice_id=inv.ref, description=d,
                                       amount=M.money_sanitize(_g("item_amount")),
                                       quantity=int(_g("item_quantity", "1") or 1),
                                       discount=M.money_sanitize(_g("item_discount")),
                                       vat=M.money_sanitize(_g("item_vat")))
        messages.success(request, "Invoice saved.")
        return redirect("/admin/invoice/")
    return render(request, "staff/invoice_edit.html",
                  {"inv": inv, "items": items, "brands": Brand.objects.all(),
                   "gateways": Gateway.objects.all()})


@login_required
@page_required("payment_link")
def payment_link_list(request):
    qs = PaymentLink.objects.all().order_by("-id")
    if request.method == "POST" and "delete" in request.POST:
        if not has_permission(request.pp_permissions, "payment_link", "delete", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        PaymentLink.objects.filter(ref=request.POST["delete"]).delete()
        return redirect("/admin/payment-link/")
    return render(request, "staff/payment_link_list.html", {"page": _paginate(request, qs)})


@login_required
@page_required("payment_link")
def payment_link_edit(request, ref: str = ""):
    link = PaymentLink.objects.filter(ref=ref).first() if ref else None
    if request.method == "POST":
        need = "edit" if ref else "create"
        if not has_permission(request.pp_permissions, "payment_link", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        data = {
            "brand_id": request.POST.get("brand_id", ""),
            "product_info": request.POST.get("product_info", "{}"),
            "amount": M.money_sanitize(request.POST.get("amount", "0")),
            "quantity": int(request.POST.get("quantity", "0") or 0),
            "currency": request.POST.get("currency", "BDT"),
            "expired_date": request.POST.get("expired_date", ""),
            "status": request.POST.get("status", "active"),
        }
        if link:
            for k, v in data.items():
                setattr(link, k, v)
            link.save()
        else:
            link = PaymentLink.objects.create(ref=request.POST.get("ref") or M.generate_item_id(12, 12), **data)
        messages.success(request, "Payment link saved.")
        return redirect("/admin/payment-link/")
    return render(request, "staff/payment_link_edit.html", {"link": link, "brands": Brand.objects.all()})


@login_required
@page_required("gateways")
def gateway_list(request):
    qs = Gateway.objects.all().order_by("-id")
    if request.method == "POST" and "delete" in request.POST:
        if not has_permission(request.pp_permissions, "gateways", "delete", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        gid = request.POST["delete"]
        GatewayParameter.objects.filter(gateway_id=gid).delete()
        Gateway.objects.filter(gateway_id=gid).delete()
        return redirect("/admin/gateways/")
    return render(request, "staff/gateway_list.html", {"page": _paginate(request, qs)})


@login_required
@page_required("gateways")
def gateway_edit(request, gateway_id: str = ""):
    gw = Gateway.objects.filter(gateway_id=gateway_id).first() if gateway_id else None
    params = {p.option_name: p.value for p in GatewayParameter.objects.filter(gateway_id=gateway_id)} if gateway_id else {}
    plugins = all_gateways()
    if request.method == "POST":
        need = "edit" if gateway_id else "create"
        if not has_permission(request.pp_permissions, "gateways", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        slug = request.POST.get("slug", gw.slug if gw else "")
        plugin = plugins.get(slug)
        if gw:
            for f in ("name", "display", "currency", "status", "tab", "primary_color", "text_color",
                      "btn_color", "btn_text_color"):
                if f in request.POST:
                    setattr(gw, f, request.POST[f])
            for f in ("min_allow", "max_allow", "fixed_discount", "percentage_discount", "fixed_charge", "percentage_charge"):
                if f in request.POST:
                    setattr(gw, f, M.money_sanitize(request.POST[f]))
            gw.save()
            gid = gw.gateway_id
        else:
            gid = M.generate_item_id()
            info = plugin.info() if plugin else {}
            color = plugin.color() if plugin else {}
            gw = Gateway.objects.create(
                gateway_id=gid, brand_id=request.POST.get("brand_id", ""), slug=slug,
                name=request.POST.get("name", info.get("title", slug)),
                display=request.POST.get("display", info.get("title", slug)),
                currency=request.POST.get("currency", info.get("currency", "BDT")),
                tab=request.POST.get("tab", info.get("tab", "mfs")), status="active",
                primary_color=color.get("primary_color", ""), text_color=color.get("text_color", ""),
                btn_color=color.get("btn_color", ""), btn_text_color=color.get("btn_text_color", ""))
        for field in (plugin.fields() if plugin else []):
            name = field["name"]
            if name in request.POST or field.get("type") == "image" and request.FILES.get(name):
                if field.get("type") == "image" and request.FILES.get(name):
                    from django.core.files.storage import default_storage
                    f = request.FILES[name]
                    path = default_storage.save(f"gateways/{gid}_{f.name}", f)
                    value = f"/media/{path}"
                else:
                    value = request.POST.get(name, "")
                GatewayParameter.objects.update_or_create(gateway_id=gid, option_name=name, defaults={"value": value, "brand_id": gw.brand_id})
        messages.success(request, "Gateway saved.")
        return redirect("/admin/gateways/")
    return render(request, "staff/gateway_edit.html", {"gw": gw, "params": params, "plugins": plugins,
                                                       "brands": Brand.objects.all()})


@login_required
@page_required("sms_data")
def sms_data(request):
    qs = SmsData.objects.all().order_by("-id")
    if request.method == "POST":
        if "delete" in request.POST:
            if not has_permission(request.pp_permissions, "sms_data", "delete", request.pp_admin.role):
                return render(request, "staff/denied.html", status=403)
            SmsData.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/sms-data/")
        # manual entry (entry_type=manual, mirrors PHP sms-data.php create)
        if not has_permission(request.pp_permissions, "sms_data", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        SmsData.objects.create(
            source="web", device_id=request.POST.get("device_id", "--"),
            sender=request.POST.get("sender", "--"), sender_key=request.POST.get("sender_key", "--"),
            simslot=request.POST.get("simslot", ""), number=request.POST.get("number", "--"),
            amount=M.money_sanitize(request.POST.get("amount", "0")), currency=request.POST.get("currency", "--"),
            trx_id=(request.POST.get("trx_id", "--") or "--").upper(),
            balance=request.POST.get("balance", "--"), message=request.POST.get("message", ""),
            type=request.POST.get("type", "Personal"), entry_type="manual", status="approved")
        return redirect("/admin/sms-data/")
    return render(request, "staff/sms_data.html", {"page": _paginate(request, qs)})


@login_required
@page_required("device")
def devices(request):
    qs = Device.objects.all().order_by("-id")
    if request.method == "POST" and "delete" in request.POST:
        if not has_permission(request.pp_permissions, "device", "delete", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        Device.objects.filter(id=request.POST["delete"]).delete()
        return redirect("/admin/devices/")
    return render(request, "staff/devices.html", {"page": _paginate(request, qs)})


@login_required
@page_required("device")
def balance_verification(request):
    qs = BalanceVerification.objects.all().order_by("-id")
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "device", "balance_verification_for", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        BalanceVerification.objects.create(
            device_id=request.POST.get("device_id", ""), sender_key=request.POST.get("sender_key", ""),
            type=request.POST.get("type", "Personal"), current_balance=M.money_sanitize(request.POST.get("current_balance", "0")),
            simslot=request.POST.get("simslot", ""), status="active")
        return redirect("/admin/devices/balance-verification/")
    return render(request, "staff/balance_verification.html", {"page": _paginate(request, qs)})


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
        fields = ("identify_name", "name", "support_email_address", "support_phone_number", "support_website",
                  "whatsapp_number", "telegram", "facebook_messenger", "facebook_page", "theme",
                  "street_address", "city_town", "postal_code", "country", "timezone", "language",
                  "currency_code", "autoExchange", "payment_tolerance")
        data = {f: request.POST.get(f, "") for f in fields}
        if brand:
            for k, v in data.items():
                setattr(brand, k, v)
            brand.save()
        else:
            brand = Brand.objects.create(brand_id=M.generate_item_id(), **data)
        return redirect("/admin/brands/")
    return render(request, "staff/brand_edit.html", {"brand": brand})


@login_required
@page_required("brand_settings")
def brand_general(request):
    brand = Brand.objects.first()
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "brand_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        for f in ("identify_name", "name", "timezone", "language", "currency_code", "payment_tolerance"):
            if brand and f in request.POST:
                setattr(brand, f, request.POST[f])
        if brand:
            brand.save()
        return redirect("/admin/brand-setting/general/")
    return render(request, "staff/brand_general.html", {"brand": brand})


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
        if not has_permission(request.pp_permissions, "api_settings", "create", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        import secrets as _s
        ApiKey.objects.create(brand_id=request.POST.get("brand_id", ""), name=request.POST.get("name", ""),
                              api_key="pp_" + _s.token_urlsafe(40)[:50],
                              expired_date=request.POST.get("expired_date") or "--",
                              api_scopes=json.dumps(request.POST.getlist("scopes") or ["create_payment", "verify_payment"]),
                              status="active")
        return redirect("/admin/brand-setting/api/")
    return render(request, "staff/brand_api.html", {"keys": keys, "brands": Brand.objects.all()})


@login_required
@page_required("brand_settings")
def brand_currency(request):
    qs = Currency.objects.all().order_by("brand_id", "code")
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "currency_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        Currency.objects.update_or_create(brand_id=request.POST.get("brand_id", ""), code=request.POST.get("code", ""),
                                          defaults={"symbol": request.POST.get("symbol", ""),
                                                    "rate": M.money_sanitize(request.POST.get("rate", "0"))})
        return redirect("/admin/brand-setting/currency/")
    return render(request, "staff/brand_currency.html", {"currencies": qs, "brands": Brand.objects.all()})


@login_required
@page_required("brand_settings")
def brand_faq(request):
    qs = Faq.objects.all().order_by("-id")
    if request.method == "POST":
        if "delete" in request.POST:
            Faq.objects.filter(id=request.POST["delete"]).delete()
            return redirect("/admin/brand-setting/faq/")
        Faq.objects.create(brand_id=request.POST.get("brand_id", ""), title=request.POST.get("title", ""),
                           description=request.POST.get("description", ""), status="active")
        return redirect("/admin/brand-setting/faq/")
    return render(request, "staff/brand_faq.html", {"faqs": qs, "brands": Brand.objects.all()})


@login_required
@page_required("brand_settings")
def brand_themes(request):
    from themes.twenty_six import TwentySixTheme
    theme = TwentySixTheme()
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "theme_settings", "edit", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        brand_id = request.POST.get("brand_id", "both")
        for field in theme.fields():
            if field["name"] in request.POST:
                set_env(f"theme-{field['name']}", request.POST[field["name"]], brand_id)
        messages.success(request, "Theme settings saved.")
        return redirect("/admin/brand-setting/themes/")
    saved = {f["name"]: get_env(f"theme-{f['name']}") for f in theme.fields()}
    return render(request, "staff/brand_themes.html",
                  {"fields": theme.fields(), "saved": saved, "brands": Brand.objects.all()})


@login_required
@page_required("staff_management")
def staff_list(request):
    qs = Admin.objects.all().order_by("-id")
    if request.method == "POST" and "delete" in request.POST:
        if not has_permission(request.pp_permissions, "staff", "delete", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        Admin.objects.filter(a_id=request.POST["delete"]).exclude(role="admin").delete()
        return redirect("/admin/staff/")
    return render(request, "staff/staff_list.html", {"page": _paginate(request, qs)})


@login_required
@page_required("staff_management")
def staff_edit(request, a_id: str = ""):
    member = Admin.objects.filter(a_id=a_id).first() if a_id else None
    brands = Brand.objects.all()
    assigned = []
    if member:
        assigned = [p.brand_id for p in Permission.objects.filter(a_id=a_id)]
    if request.method == "POST":
        need = "edit" if a_id else "create"
        if not has_permission(request.pp_permissions, "staff", need, request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        if member:
            member.full_name = request.POST.get("full_name", member.full_name)
            member.email = request.POST.get("email", member.email)
            member.status = request.POST.get("status", member.status)
            if request.POST.get("password"):
                member.password = make_password(request.POST["password"])
            member.save()
        else:
            member = Admin.objects.create(a_id=M.generate_item_id(), full_name=request.POST.get("full_name", ""),
                                          username=request.POST.get("username", ""), email=request.POST.get("email", ""),
                                          password=make_password(request.POST.get("password", M.generate_strong_password())),
                                          role="staff", status="active")
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
def staff_permissions(request, a_id: str):
    member = Admin.objects.filter(a_id=a_id).first()
    if not member:
        return render(request, "core/404.html", status=404)
    row = Permission.objects.filter(a_id=a_id).first()
    current = json.loads(row.permission) if row and row.permission else permission_schema()
    schema = permission_schema()
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "staff", "edit_permission", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        new = {"resources": {}, "pages": {}}
        for module, actions in schema["resources"].items():
            new["resources"][module] = {a: bool(request.POST.get(f"res_{module}_{a}")) for a in actions}
        for page in schema["pages"]:
            new["pages"][page] = bool(request.POST.get(f"page_{page}"))
        Permission.objects.update_or_create(a_id=a_id, defaults={"brand_id": request.POST.get("brand_id", "both"),
                                                                  "permission": json.dumps(new)})
        messages.success(request, "Permissions updated.")
        return redirect(f"/admin/staff/{a_id}/permissions/")
    return render(request, "staff/staff_permissions.html", {"member": member, "schema": schema, "current": current})


@login_required
def reports(request):
    if not can_access_page(request.pp_permissions, "reports", request.pp_admin.role):
        return render(request, "staff/denied.html", status=403)
    by_status = list(Transaction.objects.values("status").annotate(n=Count("id"), total=Sum("amount")))
    by_gateway = list(Transaction.objects.values("gateway_id").annotate(n=Count("id"), total=Sum("amount")))
    return render(request, "staff/reports.html", {"by_status": by_status, "by_gateway": by_gateway})


@login_required
def activities(request):
    logs = BrowserLog.objects.all().order_by("-id")
    hooks_list = WebhookLog.objects.all().order_by("-id")[:50]
    return render(request, "staff/activities.html", {"page": _paginate(request, logs), "webhooks": hooks_list})


@login_required
def my_account(request):
    admin = request.pp_admin
    if request.method == "POST":
        admin.full_name = request.POST.get("full_name", admin.full_name)
        admin.email = request.POST.get("email", admin.email)
        if request.POST.get("password"):
            admin.password = make_password(request.POST["password"])
        # 2FA toggle
        if request.POST.get("twofa") == "enable" and admin.twofa_status != "enable":
            admin.twofa_secret = generate_secret()
            admin.twofa_status = "enable"
        elif request.POST.get("twofa") == "disable":
            admin.twofa_status = "disable"
        admin.save()
        messages.success(request, "Account updated.")
        return redirect("/admin/my-account/")
    qr_url = otpauth_url(admin.twofa_secret, admin.username) if admin.twofa_status == "enable" else ""
    return render(request, "staff/my_account.html", {"admin": admin, "qr_url": qr_url})


@login_required
def system_settings(request):
    section = request.GET.get("section", "general")
    if request.method == "POST":
        if not has_permission(request.pp_permissions, "system_settings", "manage_general", request.pp_admin.role):
            return render(request, "staff/denied.html", status=403)
        for k, v in request.POST.items():
            if k.startswith("env_"):
                set_env(k[4:], v)
        messages.success(request, "Settings saved.")
        return redirect(f"/admin/system-settings/?section={section}")
    ctx = {"section": section,
           "cron_token": get_env("cron-job"),
           "webhook_limit": get_env("geneal-application-settings-webhook_attempts_limit"),
           "auto_update": get_env("system-settings-automatic_update"),
           "update_channel": get_env("system-settings-update_channel")}
    return render(request, "staff/system_settings.html", ctx)


@login_required
def logout_view(request):
    request.session.flush()
    return redirect("/login/")
