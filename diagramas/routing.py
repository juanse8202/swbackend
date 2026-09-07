from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/diagramas/(?P<diagrama_id>\w+)/$', consumers.DiagramaConsumer.as_asgi()),
]