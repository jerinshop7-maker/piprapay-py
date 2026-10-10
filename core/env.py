"""
get_env()/set_env() port — thin wrapper over EnvOption (pp_env table).
brand_id='both' is the global scope, exactly like PHP.
"""
from __future__ import annotations

from core.models import EnvOption


def get_env(option_name: str, brand_id: str = "both") -> str:
    try:
        row = EnvOption.objects.filter(option_name=option_name, brand_id=brand_id).first()
        if row:
            return row.value
        if brand_id != "both":
            row = EnvOption.objects.filter(option_name=option_name, brand_id="both").first()
            if row:
                return row.value
        return "--"
    except Exception:
        return "--"


def set_env(option_name: str, value: str, brand_id: str = "both") -> None:
    EnvOption.objects.update_or_create(
        option_name=option_name, brand_id=brand_id, defaults={"value": str(value)}
    )
