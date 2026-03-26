from django.urls import re_path

from core.consumers import LiveUpdatesConsumer


websocket_urlpatterns = [
    re_path(r'^ws/live-updates/$', LiveUpdatesConsumer.as_asgi()),
]
