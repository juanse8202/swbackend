from django.db import models
from django.contrib.auth.models import User

class Proyecto(models.Model):
    nombre = models.CharField(max_length=200)
    creador = models.ForeignKey(User, on_delete=models.CASCADE, related_name='proyectos_creados')
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre


class ProyectoMiembro(models.Model):
    class Rol(models.TextChoices):
        PROPIETARIO = 'propietario', 'Propietario'
        ARQUITECTO = 'arquitecto', 'Arquitecto'
        EDITOR = 'editor', 'Editor'
        LECTOR = 'lector', 'Solo lectura'

    proyecto = models.ForeignKey(
        Proyecto, on_delete=models.CASCADE, related_name='miembros'
    )
    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='membresias_proyecto'
    )
    rol = models.CharField(max_length=20, choices=Rol.choices, default=Rol.EDITOR)
    fecha_union = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['proyecto', 'usuario'], name='proyecto_miembro_unico'
            )
        ]

    def __str__(self):
        return f'{self.usuario} en {self.proyecto}: {self.rol}'
