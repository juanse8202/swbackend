from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .usernames import username_from_email


class RegistroSerializer(serializers.Serializer):
    """Registra usuarios solicitando solo correo y contrasena."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        email = value.strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "Ya existe una cuenta registrada con este correo."
            )
        return email

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        UserModel = get_user_model()
        email = validated_data["email"]
        return UserModel.objects.create_user(
            username=username_from_email(email),
            email=email,
            password=validated_data["password"],
        )
