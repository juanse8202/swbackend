"""Autenticacion basada en las sesiones nativas de Django."""

from django.contrib.auth import get_user_model, login
from django.contrib.auth.backends import ModelBackend
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RegistroSerializer


class EmailOrUsernameBackend(ModelBackend):
    """Permite autenticar con el nombre de usuario o con el correo."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get("email")
        if not identifier or password is None:
            return None

        UserModel = get_user_model()
        user = (
            UserModel.objects.filter(email__iexact=identifier).first()
            if "@" in identifier
            else UserModel.objects.filter(username=identifier).first()
        )
        if user is not None and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfTokenView(APIView):
    """Inicializa la cookie CSRF necesaria para solicitudes seguras."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class CurrentUserView(APIView):
    """Devuelve el usuario autenticado mediante la sesion actual."""

    def get(self, request):
        user = request.user
        return Response({
            "id": user.pk,
            "username": user.get_username(),
            "email": user.email,
        })


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    """Inicia una sesion con ``username`` (usuario o correo) y ``password``."""

    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username") or request.data.get("email")
        password = request.data.get("password")

        from django.contrib.auth import authenticate

        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {"detail": "Usuario/correo o contrasena incorrectos."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        login(request, user)
        return Response({
            "detail": "Sesion iniciada.",
            "user": {"id": user.pk, "username": user.get_username(), "email": user.email},
        })


@method_decorator(csrf_protect, name="dispatch")
class RegistroView(APIView):
    """Registra una cuenta usando solo correo y contrasena."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistroSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        login(
            request,
            user,
            backend='django.contrib.auth.backends.ModelBackend',
        )
        return Response(
            {
                "detail": "Cuenta creada y sesion iniciada.",
                "user": {
                    "id": user.pk,
                    "username": user.get_username(),
                    "email": user.email,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class LogoutView(APIView):
    """Cierra la sesion actual."""

    def post(self, request):
        from django.contrib.auth import logout

        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)
