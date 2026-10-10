"""Pending-transaction badge count (mirrors the nav badge query in
pp-content/pp-admin/index.php:296-305). Only computed for /admin/ pages."""


def pending_badge(request):
    if not request.path.startswith("/admin/"):
        return {}
    try:
        from core.models import Transaction
        return {"pending_count": Transaction.objects.filter(status="pending").count()}
    except Exception:
        return {}
