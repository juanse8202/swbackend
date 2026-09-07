from allauth.account.adapter import DefaultAccountAdapter

from .usernames import username_from_email


class EmailUsernameAccountAdapter(DefaultAccountAdapter):
    """Hace que allauth genere el username desde el correo tambien en OAuth."""

    def populate_username(self, request, user):
        if user.email:
            user.username = username_from_email(user.email)
        else:
            super().populate_username(request, user)
