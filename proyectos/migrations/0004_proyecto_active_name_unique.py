from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


def archive_existing_duplicates(apps, schema_editor):
    """Conserva el proyecto activo más reciente de cada dueño/nombre.

    La restricción nueva no se puede instalar si existen duplicados históricos.
    Se archivan (no se eliminan) para que queden disponibles en la papelera.
    """
    Proyecto = apps.get_model('proyectos', 'Proyecto')
    seen = set()
    duplicates = []
    projects = Proyecto.objects.filter(archivado=False).annotate(
        nombre_normalizado=Lower('nombre')
    ).order_by('creador_id', 'nombre_normalizado', '-updated_at', '-id')
    for project in projects.iterator():
        key = (project.creador_id, project.nombre_normalizado)
        if key in seen:
            duplicates.append(project.pk)
        else:
            seen.add(key)
    if duplicates:
        Proyecto.objects.filter(pk__in=duplicates).update(archivado=True)


class Migration(migrations.Migration):
    dependencies = [
        ('proyectos', '0003_project_lifecycle_and_invitations'),
    ]

    operations = [
        migrations.RunPython(archive_existing_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='proyecto',
            constraint=models.UniqueConstraint(
                Lower('nombre'), 'creador', condition=Q(archivado=False),
                name='nombre_proyecto_activo_unico_por_creador',
            ),
        ),
    ]
