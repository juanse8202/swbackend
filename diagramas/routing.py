from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # Alias compatible con el cliente React existente.
    re_path(r'ws/diagrams/(?P<diagrama_id>\w+)/$', consumers.DiagramaConsumer.as_asgi()),
    re_path(r'ws/diagramas/(?P<diagrama_id>\w+)/$', consumers.DiagramaConsumer.as_asgi()),
]
