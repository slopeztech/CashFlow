"""
ASGI config for CashFlow project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see

django_asgi_app = get_asgi_application()

https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CashFlow.settings')


def _env_bool(name, default=False):
	value = os.getenv(name)
	if value is None:
		return default
	return value.strip().lower() in {'1', 'true', 'yes', 'on'}


django_asgi_app = get_asgi_application()

application = django_asgi_app

if _env_bool('ENABLE_REALTIME', default=True):
	try:
		from channels.auth import AuthMiddlewareStack
		from channels.routing import ProtocolTypeRouter, URLRouter

		from CashFlow.routing import websocket_urlpatterns

		application = ProtocolTypeRouter(
			{
				'http': django_asgi_app,
				'websocket': AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
			}
		)
	except ImportError:
		# Realtime is optional for platforms where Channels/Daphne are unavailable.
		application = django_asgi_app
