"""Operaciones de creación de proyectos colaborativos."""

from django.db import transaction


@transaction.atomic
def create_project_with_main_diagram(*, creator, nombre):
    """Crea únicamente el proyecto solicitado y su diagrama principal."""
    from diagramas.models import Diagrama
    from .models import Proyecto, ProyectoMiembro

    project = Proyecto.objects.create(creador=creator, nombre=nombre)
    # The post_save signal creates this membership in normal operation.  Keep
    # the service explicit and idempotent without creating a duplicate row.
    ProyectoMiembro.objects.get_or_create(
        proyecto=project, usuario=creator, rol=ProyectoMiembro.Rol.PROPIETARIO,
    )
    Diagrama.objects.create(proyecto=project, nombre='Diagrama principal')
    return project
