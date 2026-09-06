from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
)
from rest_framework_simplejwt.views import TokenObtainPairView


User = get_user_model()


class EmailOrUsernameTokenObtainPairSerializer(
    TokenObtainPairSerializer
):
    """
    Permite iniciar sesión utilizando el username o el correo.
    """

    def validate(self, attrs):
        login = attrs.get(self.username_field)

        # Si el dato recibido parece un correo, buscamos su username.
        if login and "@" in login:
            user = User.objects.filter(
                email__iexact=login
            ).first()

            if user is not None:
                attrs[self.username_field] = user.get_username()

        return super().validate(attrs)


class EmailOrUsernameTokenObtainPairView(
    TokenObtainPairView
):
    serializer_class = EmailOrUsernameTokenObtainPairSerializer