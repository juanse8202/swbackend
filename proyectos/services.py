"""Operaciones de creación del espacio de trabajo colaborativo."""

from django.db import transaction


@transaction.atomic
def create_project_with_main_diagram(*, creator, nombre):
    """Crea un proyecto y el único lienzo inicial que sus miembros compartirán."""
    from diagramas.models import Diagrama
    from .models import Proyecto

    project = Proyecto.objects.create(creador=creator, nombre=nombre)
    Diagrama.objects.create(proyecto=project, nombre='Diagrama principal')
    return project


@transaction.atomic
def get_or_create_personal_workspace(user):
    """Devuelve el lienzo privado inicial de una cuenta.

    No tiene colaboradores: cada usuario ve su propio espacio hasta que cree
    un proyecto compartido e invite a otra persona.
    """
    from diagramas.models import AtributoUML, ClaseUML, Diagrama, RelacionUML
    from .models import Proyecto

    project, _ = Proyecto.objects.get_or_create(
        creador=user,
        nombre='Mi lienzo personal',
    )
    diagram, _ = Diagrama.objects.get_or_create(
        proyecto=project,
        nombre='Diagrama principal',
    )

    # El primer lienzo es una plantilla privada, no datos compartidos. Permite
    # que el editor abra con contenido útil y cada usuario puede modificarlo.
    if not diagram.clases.exists():
        usuario = ClaseUML.objects.create(
            diagrama=diagram, nombre='Usuario', posicion_x=250, posicion_y=300
        )
        proyecto_clase = ClaseUML.objects.create(
            diagrama=diagram, nombre='Proyecto', posicion_x=700, posicion_y=120
        )
        AtributoUML.objects.bulk_create([
            AtributoUML(clase=usuario, nombre='id', tipo_dato='UUID', visibilidad='Public'),
            AtributoUML(clase=usuario, nombre='nombre', tipo_dato='String', visibilidad='Public'),
            AtributoUML(clase=usuario, nombre='email', tipo_dato='String', visibilidad='Public'),
            AtributoUML(clase=proyecto_clase, nombre='id', tipo_dato='UUID', visibilidad='Public'),
            AtributoUML(clase=proyecto_clase, nombre='nombre', tipo_dato='String', visibilidad='Public'),
            AtributoUML(clase=proyecto_clase, nombre='direccion', tipo_dato='String', visibilidad='Public'),
        ])
        RelacionUML.objects.create(
            diagrama=diagram,
            clase_origen=usuario,
            clase_destino=proyecto_clase,
            tipo='Asociacion',
            multiplicidad_origen='1',
            multiplicidad_destino='N',
        )
    return project
