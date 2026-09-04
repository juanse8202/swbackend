from django.db import models
from django.contrib.auth.models import User

class Proyecto(models.Model):
    nombre = models.CharField(max_length=200)
    creador = models.ForeignKey(User, on_delete=models.CASCADE, related_name='proyectos_creados')
    colaboradores = models.ManyToManyField(User, related_name='proyectos_colaborativos', blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre