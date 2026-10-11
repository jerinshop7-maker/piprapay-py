"""Display helpers mirroring PHP's NULL-cell -> '--' mapping (getData maps
every NULL cell to '--' on read)."""
from django import template

register = template.Library()


@register.filter
def pp(value):
    """Render None/empty exactly like PHP cells: '--' for NULL, raw otherwise."""
    if value is None or value == "":
        return "--"
    return value
