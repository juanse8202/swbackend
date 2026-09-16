from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('diagramas', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='diagrama',
            name='nodes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='diagrama',
            name='edges',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
