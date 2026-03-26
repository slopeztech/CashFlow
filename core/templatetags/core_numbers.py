from decimal import Decimal, InvalidOperation, ROUND_DOWN

from django import template

register = template.Library()


@register.filter
def trim_zeros(value):
    if value is None:
        return ''

    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value

    text = format(numeric.normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


@register.filter
def truncate_2(value):
    if value in (None, ''):
        return '0.00'

    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value

    return numeric.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
