from django.shortcuts import resolve_url
from django.utils.http import url_has_allowed_host_and_scheme


def safe_redirect_target(request, next_value, fallback):
    if next_value and url_has_allowed_host_and_scheme(
        url=next_value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_value
    return resolve_url(fallback)
