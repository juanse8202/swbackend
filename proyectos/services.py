"""Operaciones de creación de proyectos colaborativos."""

from django.db import transaction


@transaction.atomic
def create_project_with_main_diagram(*, creator, nombre):
    """Crea únicamente el proyecto solicitado y su diagrama principal."""
    from diagramas.models import Diagrama
    from .models import Proyecto

    project = Proyecto.objects.create(creador=creator, nombre=nombre)
    Diagrama.objects.create(proyecto=project, nombre='Diagrama principal')
    return project
