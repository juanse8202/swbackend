"""Utilidades para construir nombres de usuario a partir del correo."""

from django.contrib.auth import get_user_model


def username_from_email(email):
    """Devuelve la parte previa a ``@`` y agrega un numero si ya existe."""
    UserModel = get_user_model()
    max_length = UserModel._meta.get_field("username").max_length
    base = email.split("@", 1)[0].strip() or "usuario"
    base = base[:max_length]
    username = base
    number = 1

    while UserModel.objects.filter(username__iexact=username).exists():
        suffix = str(number)
        username = f"{base[:max_length - len(suffix)]}{suffix}"
        number += 1

    return username
