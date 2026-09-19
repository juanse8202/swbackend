from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('proyectos', '0002_proyectomiembro_roles'),
    ]

    operations = [
        migrations.AddField(
            model_name='proyecto', name='archivado',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='proyecto', name='is_template',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='proyecto', name='updated_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='proyecto', name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.CreateModel(
            name='InvitacionProyecto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rol', models.CharField(choices=[('propietario', 'Propietario'), ('arquitecto', 'Arquitecto'), ('editor', 'Editor'), ('lector', 'Solo lectura')], max_length=20)),
                ('estado', models.CharField(choices=[('pendiente', 'Pendiente'), ('aceptada', 'Aceptada'), ('rechazada', 'Rechazada')], default='pendiente', max_length=12)),
                ('fecha', models.DateTimeField(auto_now_add=True)),
                ('respondida_en', models.DateTimeField(blank=True, null=True)),
                ('invitado', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invitaciones_proyecto', to=settings.AUTH_USER_MODEL)),
                ('invitador', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invitaciones_enviadas', to=settings.AUTH_USER_MODEL)),
                ('proyecto', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invitaciones', to='proyectos.proyecto')),
            ],
            options={'ordering': ['-fecha']},
        ),
        migrations.AddConstraint(
            model_name='invitacionproyecto',
            constraint=models.UniqueConstraint(
                condition=models.Q(('estado', 'pendiente')),
                fields=('proyecto', 'invitado'),
                name='invitacion_proyecto_pendiente_unica',
            ),
        ),
    ]
