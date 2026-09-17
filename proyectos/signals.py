"""Señales del módulo de proyectos."""

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import get_or_create_personal_workspace
from .models import Proyecto, ProyectoMiembro


@receiver(post_save, sender=get_user_model())
def create_personal_workspace(sender, instance, created, **kwargs):
    """Cada cuenta recibe un lienzo personal, independiente y sin compartir."""
    if created:
        get_or_create_personal_workspace(instance)


@receiver(post_save, sender=Proyecto)
def create_owner_membership(sender, instance, created, **kwargs):
    """Todo proyecto nuevo tiene exactamente un propietario inicial."""
    if created:
        ProyectoMiembro.objects.get_or_create(
            proyecto=instance,
            usuario=instance.creador,
            defaults={'rol': ProyectoMiembro.Rol.PROPIETARIO},
        )
