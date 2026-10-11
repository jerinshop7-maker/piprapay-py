"""
WordPress-style hook system: add_action/do_action/add_filter/apply_filters.
Port of pp-functions.php hooks. Like PHP, each callback runs crash-isolated so
one failing callback cannot abort the remaining chain.
"""
from __future__ import annotations

_actions: dict[str, list[tuple[int, callable]]] = {}
_filters: dict[str, list[tuple[int, callable]]] = {}


def add_action(hook: str, callback, priority: int = 10) -> None:
    _actions.setdefault(hook, []).append((priority, callback))
    _actions[hook].sort(key=lambda x: x[0])


def do_action(hook: str, *args, **kwargs) -> None:
    for _, cb in sorted(_actions.get(hook, []), key=lambda x: x[0]):
        try:
            cb(*args, **kwargs)
        except Exception:
            continue


def add_filter(hook: str, callback, priority: int = 10) -> None:
    _filters.setdefault(hook, []).append((priority, callback))
    _filters[hook].sort(key=lambda x: x[0])


def apply_filters(hook: str, value, *args, **kwargs):
    for _, cb in sorted(_filters.get(hook, []), key=lambda x: x[0]):
        try:
            value = cb(value, *args, **kwargs)
        except Exception:
            continue
    return value


# Default filters mirroring pp-functions.php bottom registrations
def _invoice_total_default(total, invoice):
    return total


add_filter("invoice.total", _invoice_total_default)
