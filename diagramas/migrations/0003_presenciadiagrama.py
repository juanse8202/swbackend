from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('diagramas', '0002_diagrama_nodes_edges'),
    ]

    operations = [
        migrations.CreateModel(
            name='PresenciaDiagrama',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel_name', models.CharField(max_length=255, unique=True)),
                ('fecha_conexion', models.DateTimeField(auto_now_add=True)),
                ('diagrama', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='presencias', to='diagramas.diagrama')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name='presenciadiagrama',
            index=models.Index(fields=['diagrama', 'usuario'], name='diagramas_p_diagram_8d94cd_idx'),
        ),
    ]
