from django.apps import AppConfig


class ProyectosConfig(AppConfig):
    name = 'proyectos'

    def ready(self):
        from . import signals  # noqa: F401
