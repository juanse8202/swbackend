from django.db import models
from django.contrib.auth.models import User

class Diagrama(models.Model):
    proyecto = models.ForeignKey('proyectos.Proyecto', on_delete=models.CASCADE, related_name='diagramas')
    nombre = models.CharField(max_length=200)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nombre

class ClaseUML(models.Model):
    diagrama = models.ForeignKey(Diagrama, on_delete=models.CASCADE, related_name='clases')
    nombre = models.CharField(max_length=100)
    posicion_x = models.FloatField(default=0.0)
    posicion_y = models.FloatField(default=0.0)

    def __str__(self):
        return f"{self.nombre} ({self.diagrama.nombre})"

class AtributoUML(models.Model):
    VISIBILIDAD_CHOICES = (
        ('Public', 'Public (+)'),
        ('Private', 'Private (-)'),
        ('Protected', 'Protected (#)'),
    )
    clase = models.ForeignKey(ClaseUML, on_delete=models.CASCADE, related_name='atributos')
    nombre = models.CharField(max_length=100)
    tipo_dato = models.CharField(max_length=50)
    visibilidad = models.CharField(max_length=20, choices=VISIBILIDAD_CHOICES, default='Private')

class RelacionUML(models.Model):
    TIPO_CHOICES = (
        ('Asociacion', 'Asociación'),
        ('Herencia', 'Herencia'),
        ('Composicion', 'Composición'),
        ('Agregacion', 'Agregación'),
        ('Dependencia', 'Dependencia'),
    )
    diagrama = models.ForeignKey(Diagrama, on_delete=models.CASCADE, related_name='relaciones')
    clase_origen = models.ForeignKey(ClaseUML, on_delete=models.CASCADE, related_name='relaciones_salientes')
    clase_destino = models.ForeignKey(ClaseUML, on_delete=models.CASCADE, related_name='relaciones_entrantes')
    tipo = models.CharField(max_length=50, choices=TIPO_CHOICES)
    multiplicidad_origen = models.CharField(max_length=10, blank=True, null=True)
    multiplicidad_destino = models.CharField(max_length=10, blank=True, null=True)

class VersionDiagrama(models.Model):
    diagrama = models.ForeignKey(Diagrama, on_delete=models.CASCADE, related_name='versiones')
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    datos_snapshot = models.JSONField()
    fecha_creacion = models.DateTimeField(auto_now_add=True)