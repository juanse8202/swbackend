"""Servicios para el espacio de trabajo inicial de cada usuario."""


def get_default_diagram(user):
    """Obtiene o crea el proyecto y lienzo privado inicial del usuario."""
    from diagramas.models import Diagrama

    from .models import Proyecto

    project, _ = Proyecto.objects.get_or_create(
        creador=user,
        nombre="Mi proyecto",
    )
    diagram, _ = Diagrama.objects.get_or_create(
        proyecto=project,
        nombre="Mi diagrama",
    )
    return diagram
