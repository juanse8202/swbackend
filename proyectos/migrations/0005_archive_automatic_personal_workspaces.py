from django.db import migrations


def archive_automatic_personal_workspaces(apps, schema_editor):
    """Oculta los lienzos que versiones previas creaban sin solicitarlo."""
    Proyecto = apps.get_model('proyectos', 'Proyecto')
    Proyecto.objects.filter(
        nombre='Mi lienzo personal', archivado=False
    ).update(archivado=True)


class Migration(migrations.Migration):
    dependencies = [
        ('proyectos', '0004_proyecto_active_name_unique'),
    ]

    operations = [
        migrations.RunPython(
            archive_automatic_personal_workspaces, migrations.RunPython.noop
        ),
    ]
