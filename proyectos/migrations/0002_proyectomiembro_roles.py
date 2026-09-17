from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_memberships(apps, schema_editor):
    Proyecto = apps.get_model('proyectos', 'Proyecto')
    ProyectoMiembro = apps.get_model('proyectos', 'ProyectoMiembro')

    for proyecto in Proyecto.objects.all().iterator():
        ProyectoMiembro.objects.get_or_create(
            proyecto_id=proyecto.id,
            usuario_id=proyecto.creador_id,
            defaults={'rol': 'propietario'},
        )
        for usuario_id in proyecto.colaboradores.values_list('id', flat=True):
            ProyectoMiembro.objects.get_or_create(
                proyecto_id=proyecto.id,
                usuario_id=usuario_id,
                defaults={'rol': 'editor'},
            )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('proyectos', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProyectoMiembro',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rol', models.CharField(choices=[('propietario', 'Propietario'), ('arquitecto', 'Arquitecto'), ('editor', 'Editor'), ('lector', 'Solo lectura')], default='editor', max_length=20)),
                ('fecha_union', models.DateTimeField(auto_now_add=True)),
                ('proyecto', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='miembros', to='proyectos.proyecto')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='membresias_proyecto', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='proyectomiembro',
            constraint=models.UniqueConstraint(fields=('proyecto', 'usuario'), name='proyecto_miembro_unico'),
        ),
        migrations.RunPython(migrate_memberships, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='proyecto',
            name='colaboradores',
        ),
    ]
