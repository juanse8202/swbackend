from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('diagramas', '0003_presenciadiagrama'),
    ]

    operations = [
        migrations.AddField(
            model_name='presenciadiagrama',
            name='fecha_actividad',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
