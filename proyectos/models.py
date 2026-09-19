from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.contrib.auth.models import User

class Proyecto(models.Model):
    nombre = models.CharField(max_length=200)
    creador = models.ForeignKey(User, on_delete=models.CASCADE, related_name='proyectos_creados')
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_template = models.BooleanField(default=False)
    archivado = models.BooleanField(default=False)

    class Meta:
        constraints = [
            # Dos propietarios pueden usar el mismo nombre, pero un mismo
            # propietario no puede duplicar un proyecto que siga activo.
            models.UniqueConstraint(
                Lower('nombre'), 'creador', condition=Q(archivado=False),
                name='nombre_proyecto_activo_unico_por_creador',
            ),
        ]

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


class InvitacionProyecto(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = 'pendiente', 'Pendiente'
        ACEPTADA = 'aceptada', 'Aceptada'
        RECHAZADA = 'rechazada', 'Rechazada'

    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='invitaciones')
    invitador = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invitaciones_enviadas')
    invitado = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invitaciones_proyecto')
    rol = models.CharField(max_length=20, choices=ProyectoMiembro.Rol.choices)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    fecha = models.DateTimeField(auto_now_add=True)
    respondida_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['proyecto', 'invitado'],
                condition=models.Q(estado='pendiente'),
                name='invitacion_proyecto_pendiente_unica',
            )
        ]
        ordering = ['-fecha']
