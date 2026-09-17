"""Reglas de autorizacion basadas en la membresia de un proyecto."""

from rest_framework.exceptions import PermissionDenied

from .models import ProyectoMiembro


EDIT_ROLES = {
    ProyectoMiembro.Rol.PROPIETARIO,
    ProyectoMiembro.Rol.ARQUITECTO,
    ProyectoMiembro.Rol.EDITOR,
}


def role_for(user, proyecto):
    try:
        return proyecto.miembros.only('rol').get(usuario=user).rol
    except ProyectoMiembro.DoesNotExist:
        return None


def require_role(user, proyecto, allowed_roles, message='No tienes permiso para esta accion.'):
    if role_for(user, proyecto) not in allowed_roles:
        raise PermissionDenied(message)


def require_owner(user, proyecto):
    require_role(
        user,
        proyecto,
        {ProyectoMiembro.Rol.PROPIETARIO},
        'Solo el propietario puede administrar este proyecto.',
    )
