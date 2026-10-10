"""
Port of permissionSchema()/hasPermission()/canAccessPage()/countPermissions()
from pp-functions.php. Identical schema so staff UX is 1:1.
"""
from __future__ import annotations

PERMISSION_SCHEMA = {
    "resources": {
        "customers": {"create": True, "edit": True, "delete": True},
        "transaction": {"edit": True, "delete": True, "approve": True, "cancel": True, "refund": True, "send_ipn": True},
        "invoice": {"create": True, "edit": True, "delete": True},
        "payment_link": {"create": True, "edit": True, "delete": True},
        "gateways": {"create": True, "edit": True, "delete": True},
        "addons": {"create": True, "edit": True, "delete": True},
        "brand_settings": {"view": True, "edit": True},
        "api_settings": {"view": True, "create": True, "edit": True, "delete": True},
        "theme_settings": {"view": True, "edit": True},
        "faq_settings": {"view": True, "create": True, "edit": True, "delete": True},
        "currency_settings": {"view": True, "sync_rate": True, "import": True, "edit": True},
        "sms_data": {"create": True, "edit": True, "delete": True},
        "device": {"connect": True, "delete": True, "balance_verification_for": True},
        "brands": {"create": True, "edit": True, "delete": True},
        "staff": {
            "create": True, "edit": True, "delete": True, "assign_brand_to": True,
            "edit_permission": True, "view_permission_list": True, "delete_permission_of": True,
        },
        "domains": {"whitelist": True, "edit": True, "delete": True},
        "system_settings": {"manage_general": True, "manage_cron": True, "manage_update": True, "manage_import": True},
    },
    "pages": {
        "dashboard": True, "reports": True, "customers": True, "transaction": True,
        "invoice": True, "payment_link": True, "gateways": True, "addons": True,
        "brand_settings": True, "sms_data": True, "device": True, "brands": True,
        "staff_management": True, "domains": True, "system_settings": True,
    },
}


def permission_schema() -> dict:
    import copy

    return copy.deepcopy(PERMISSION_SCHEMA)


def count_permissions(tab_key: str, tab_data: dict) -> int:
    if tab_key == "resources":
        return sum(len(actions) for actions in tab_data.values())
    if tab_key == "pages":
        return len(tab_data)
    return 0


def has_permission(permissions: dict, module: str, action: str = "view", admin_type: str = "staff") -> bool:
    if admin_type == "admin":
        return True
    try:
        return permissions.get("resources", {}).get(module, {}).get(action) is True
    except AttributeError:
        return False


def can_access_page(permissions: dict, page: str, admin_type: str = "staff") -> bool:
    if admin_type == "admin":
        return True
    try:
        return bool(permissions.get("pages", {}).get(page))
    except AttributeError:
        return False
