"""Señales del módulo de proyectos."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Proyecto, ProyectoMiembro

@receiver(post_save, sender=Proyecto)
def create_owner_membership(sender, instance, created, **kwargs):
    """Todo proyecto nuevo tiene exactamente un propietario inicial."""
    if created:
        ProyectoMiembro.objects.get_or_create(
            proyecto=instance,
            usuario=instance.creador,
            defaults={'rol': ProyectoMiembro.Rol.PROPIETARIO},
        )
