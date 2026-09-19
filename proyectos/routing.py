from django.urls import re_path

from .consumers import ProyectoConsumer


websocket_urlpatterns = [
    re_path(r'ws/proyectos/(?P<proyecto_id>\w+)/$', ProyectoConsumer.as_asgi()),
]
