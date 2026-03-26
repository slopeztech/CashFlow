from django import template

register = template.Library()


@register.filter(name='public_name')
def public_name(user):
    if not user:
        return ''
    profile = getattr(user, 'store_profile', None)
    if profile and getattr(profile, 'display_name', ''):
        return profile.display_name
    return getattr(user, 'username', '')
