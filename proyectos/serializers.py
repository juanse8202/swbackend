from rest_framework import serializers
from django.db.models import Count
from .models import InvitacionProyecto, Proyecto, ProyectoMiembro
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


class InvitacionPendienteSerializer(serializers.ModelSerializer):
    invitado = UserSerializer(read_only=True)

    class Meta:
        model = InvitacionProyecto
        fields = ['id', 'invitado', 'rol', 'fecha', 'estado']

class ProyectoSerializer(serializers.ModelSerializer):
    creador_detalle = UserSerializer(source='creador', read_only=True)
    miembros = ProyectoMiembroSerializer(many=True, read_only=True)
    colaboradores = serializers.SerializerMethodField()
    colaboradores_detalle = serializers.SerializerMethodField()
    owner = UserSerializer(source='creador', read_only=True)
    propietario = UserSerializer(source='creador', read_only=True)
    estado_invitacion = serializers.SerializerMethodField()
    rol_invitacion = serializers.SerializerMethodField()
    total_entidades = serializers.SerializerMethodField()
    total_relaciones = serializers.SerializerMethodField()
    invitaciones_pendientes = serializers.SerializerMethodField()

    def validate_nombre(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('El nombre no puede estar vacío.')
        return value

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

    def _invitation_for_request_user(self, proyecto):
        user = self.context['request'].user
        return proyecto.invitaciones.filter(invitado=user).first()

    def get_estado_invitacion(self, proyecto):
        invitation = self._invitation_for_request_user(proyecto)
        if invitation:
            return invitation.estado
        return 'aceptada' if proyecto.miembros.filter(usuario=self.context['request'].user).exists() else None

    def get_rol_invitacion(self, proyecto):
        invitation = self._invitation_for_request_user(proyecto)
        if invitation:
            return invitation.rol
        membership = proyecto.miembros.filter(usuario=self.context['request'].user).first()
        return membership.rol if membership else None

    def get_total_entidades(self, proyecto):
        # El listado las anota en SQL; el fallback cubre respuestas puntuales.
        total = getattr(proyecto, 'total_entidades', None)
        if total is not None:
            return total
        json_total = sum(len(diagrama.nodes or []) for diagrama in proyecto.diagramas.all())
        uml_total = proyecto.diagramas.aggregate(total=Count('clases', distinct=True))['total']
        return max(json_total, uml_total)

    def get_total_relaciones(self, proyecto):
        total = getattr(proyecto, 'total_relaciones', None)
        if total is not None:
            return total
        json_total = sum(len(diagrama.edges or []) for diagrama in proyecto.diagramas.all())
        uml_total = proyecto.diagramas.aggregate(total=Count('relaciones', distinct=True))['total']
        return max(json_total, uml_total)

    def get_invitaciones_pendientes(self, proyecto):
        request = self.context.get('request')
        # Solo el dueño puede ver los datos de contacto de las invitaciones
        # que ha enviado; los miembros no aceptados nunca aparecen aquí.
        if request is None or request.user != proyecto.creador:
            return []
        invitations = proyecto.invitaciones.filter(
            estado=InvitacionProyecto.Estado.PENDIENTE
        ).select_related('invitado')
        return InvitacionPendienteSerializer(invitations, many=True).data

    class Meta:
        model = Proyecto
        fields = [
            'id', 'nombre', 'creador', 'colaboradores', 'fecha_creacion',
            'creador_detalle', 'colaboradores_detalle', 'miembros', 'updated_at',
            'owner', 'propietario', 'estado_invitacion', 'rol_invitacion',
            'is_template', 'archivado', 'total_entidades', 'total_relaciones',
            'invitaciones_pendientes',
        ]
        # La membresía se administra exclusivamente mediante las acciones de
        # invitación y eliminación; no desde un PATCH genérico.
        read_only_fields = ['creador', 'colaboradores', 'fecha_creacion', 'miembros', 'updated_at', 'archivado']


class InvitacionProyectoSerializer(serializers.ModelSerializer):
    proyecto = ProyectoSerializer(read_only=True)
    invitador = UserSerializer(read_only=True)

    class Meta:
        model = InvitacionProyecto
        fields = ['id', 'proyecto', 'invitador', 'rol', 'fecha', 'estado']
