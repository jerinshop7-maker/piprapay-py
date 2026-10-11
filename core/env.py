"""
get_env()/set_env() — exact port of pp-functions.php:1403-1454.

- get_env returns '' on miss/'--' (NOT '--'), auto-INSERTs a '--' row on miss,
  and queries the EXACT brand_id only (no 'both' fallback).
- set_env upserts by (brand_id, option_name).
"""
from __future__ import annotations

from core.models import EnvOption


def get_env(option_name: str, brand_id: str = "both") -> str:
    try:
        row = EnvOption.objects.filter(option_name=option_name, brand_id=brand_id).first()
        if row is None:
            EnvOption.objects.get_or_create(option_name=option_name, brand_id=brand_id,
                                            defaults={"value": "--"})
            return ""
        if row.value == "--":
            return ""
        return row.value
    except Exception:
        return ""


def set_env(option_name: str, value: str, brand_id: str = "both") -> None:
    EnvOption.objects.update_or_create(
        option_name=option_name, brand_id=brand_id, defaults={"value": str(value)}
    )
