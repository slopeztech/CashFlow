import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils import translation

from core.models import UserSession
from customers.services import process_monthly_fee_for_user


backendlog = logging.getLogger('backendlog')

SUSPICIOUS_MARKERS = (
    '../',
    '%2e%2e',
    '<script',
    'union select',
    'or 1=1',
    '/wp-admin',
    '/phpmyadmin',
    '/.env',
)


def _safe_user_language(user):
    if not user or not user.is_authenticated:
        return None
    try:
        profile = user.store_profile
    except Exception:
        return None
    return getattr(profile, 'language', None)


class UserLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = settings.LANGUAGE_CODE
        user = getattr(request, 'user', None)
        user_language = _safe_user_language(user)
        available_languages = {code for code, _name in settings.LANGUAGES}
        if user_language in available_languages:
            language = user_language

        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()

        if user and user.is_authenticated:
            process_monthly_fee_for_user(user)

        response = self.get_response(request)
        response['Content-Language'] = translation.get_language() or language
        translation.deactivate()
        return response


class BackendSecurityLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _request_user(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            return user.username
        return 'anonymous'

    def _request_context(self, request):
        return {
            'method': request.method,
            'path': request.path,
            'query': request.META.get('QUERY_STRING', ''),
            'ip': request.META.get('REMOTE_ADDR', ''),
            'user_agent': request.META.get('HTTP_USER_AGENT', ''),
            'user': self._request_user(request),
        }

    def _is_suspicious(self, request):
        lower_path = (request.get_full_path() or '').lower()
        return any(marker in lower_path for marker in SUSPICIOUS_MARKERS)

    def __call__(self, request):
        response = self.get_response(request)
        context = self._request_context(request)

        if self._is_suspicious(request):
            backendlog.warning('Suspicious request pattern detected: %s', context)

        if response.status_code >= 400:
            backendlog.warning('HTTP error response status=%s context=%s', response.status_code, context)

        return response

    def process_exception(self, request, exception):
        backendlog.exception(
            'Unhandled exception for request context=%s exception=%s',
            self._request_context(request),
            exception,
        )
        return None


class UserSessionActivityMiddleware:
    """Persists authenticated user activity tied to the Django session key."""

    SESSION_TOUCH_KEY = '_session_activity_last_touch'
    TOUCH_INTERVAL_SECONDS = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            self._track_user_activity(request, user)
        return self.get_response(request)

    def _track_user_activity(self, request, user):
        if not request.session.session_key:
            request.session.save()

        session_key = request.session.session_key
        if not session_key:
            return

        now = timezone.now()
        last_touch_raw = request.session.get(self.SESSION_TOUCH_KEY)
        if last_touch_raw:
            try:
                last_touch = timezone.datetime.fromisoformat(last_touch_raw)
                if timezone.is_naive(last_touch):
                    last_touch = timezone.make_aware(last_touch, timezone.get_current_timezone())
            except (TypeError, ValueError):
                last_touch = None
            if last_touch and (now - last_touch) < timedelta(seconds=self.TOUCH_INTERVAL_SECONDS):
                return

        UserSession.objects.update_or_create(
            user=user,
            session_key=session_key,
            defaults={'last_activity': now},
        )
        request.session[self.SESSION_TOUCH_KEY] = now.isoformat()
