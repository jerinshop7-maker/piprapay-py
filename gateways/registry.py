"""Plugin registry — mirrors PHP dynamic class loading in index.php ipn/checkout."""
from __future__ import annotations

from gateways import plugins
from gateways.base import GatewayBase


def get_gateway(slug: str) -> GatewayBase | None:
    cls = plugins.CLASSES.get(slug)
    return cls() if cls else None


def all_gateways() -> dict[str, GatewayBase]:
    return {slug: cls() for slug, cls in plugins.CLASSES.items()}


def gateway_choices() -> list[tuple[str, str]]:
    return [(slug, gw.info().get("title", slug)) for slug, gw in all_gateways().items()]
