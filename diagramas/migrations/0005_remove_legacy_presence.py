from django.db import migrations


def remove_legacy_presence(apps, schema_editor):
    """Los canales previos a fecha_actividad no pueden considerarse activos."""
    PresenciaDiagrama = apps.get_model('diagramas', 'PresenciaDiagrama')
    PresenciaDiagrama.objects.filter(fecha_actividad__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('diagramas', '0004_presenciadiagrama_fecha_actividad'),
    ]

    operations = [
        migrations.RunPython(remove_legacy_presence, migrations.RunPython.noop),
    ]
