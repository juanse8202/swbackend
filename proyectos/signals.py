from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import get_default_diagram


@receiver(post_save, sender=get_user_model())
def create_initial_workspace(sender, instance, created, **kwargs):
    """Cada cuenta nueva, incluso las creadas con OAuth, recibe un lienzo vacio."""
    if created:
        get_default_diagram(instance)
