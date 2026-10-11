"""Display helpers mirroring PHP cell mapping (getData maps every NULL cell
to '--' on read; sms-data list maps '--' cells to empty string)."""
from django import template

register = template.Library()


@register.filter
def pp(value):
    """Render None exactly like a PHP NULL cell: '--'. Empty strings and
    stored '--' pass through untouched."""
    if value is None:
        return "--"
    return value


@register.filter
def pp_blank(value):
    """Render '--' cells as empty (sms-data list behavior)."""
    if value == "--" or value is None:
        return ""
    return value
