from rest_framework import serializers
from .models import Proyecto, ProyectoMiembro
from django.contrib.auth.models import User

# Serializer para poder listar a los usuarios (colaboradores/creadores)
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


class ProyectoMiembroSerializer(serializers.ModelSerializer):
    usuario = UserSerializer(read_only=True)

    class Meta:
        model = ProyectoMiembro
        fields = ['id', 'usuario', 'rol', 'fecha_union']

class ProyectoSerializer(serializers.ModelSerializer):
    creador_detalle = UserSerializer(source='creador', read_only=True)
    miembros = ProyectoMiembroSerializer(many=True, read_only=True)
    colaboradores = serializers.SerializerMethodField()
    colaboradores_detalle = serializers.SerializerMethodField()

    def get_colaboradores(self, proyecto):
        return list(
            proyecto.miembros.exclude(rol=ProyectoMiembro.Rol.PROPIETARIO)
            .values_list('usuario_id', flat=True)
        )

    def get_colaboradores_detalle(self, proyecto):
        return UserSerializer(
            [miembro.usuario for miembro in proyecto.miembros.exclude(rol=ProyectoMiembro.Rol.PROPIETARIO)],
            many=True,
        ).data

    class Meta:
        model = Proyecto
        fields = [
            'id', 'nombre', 'creador', 'colaboradores', 'fecha_creacion',
            'creador_detalle', 'colaboradores_detalle', 'miembros',
        ]
        # La membresía se administra exclusivamente mediante las acciones de
        # invitación y eliminación; no desde un PATCH genérico.
        read_only_fields = ['creador', 'colaboradores', 'fecha_creacion', 'miembros']
