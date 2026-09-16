from rest_framework import serializers
from .models import Proyecto
from django.contrib.auth.models import User

# Serializer para poder listar a los usuarios (colaboradores/creadores)
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']

class ProyectoSerializer(serializers.ModelSerializer):
    creador_detalle = UserSerializer(source='creador', read_only=True)
    colaboradores_detalle = UserSerializer(source='colaboradores', many=True, read_only=True)

    class Meta:
        model = Proyecto
        fields = [
            'id', 'nombre', 'creador', 'colaboradores', 'fecha_creacion',
            'creador_detalle', 'colaboradores_detalle',
        ]
        # La membresía se administra exclusivamente mediante las acciones de
        # invitación y eliminación; no desde un PATCH genérico.
        read_only_fields = ['creador', 'colaboradores', 'fecha_creacion']
