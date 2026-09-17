from django.contrib.auth.models import User
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import Proyecto, ProyectoMiembro
from .permissions import require_owner
from .serializers import ProyectoSerializer, UserSerializer
from .services import create_project_with_main_diagram, get_or_create_personal_workspace


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer


class ProyectoViewSet(viewsets.ModelViewSet):
    serializer_class = ProyectoSerializer

    def get_queryset(self):
        user = self.request.user
        get_or_create_personal_workspace(user)
        return Proyecto.objects.filter(miembros__usuario=user).distinct()

    def perform_create(self, serializer):
        serializer.instance = create_project_with_main_diagram(
            creator=self.request.user,
            nombre=serializer.validated_data['nombre'],
        )

    def perform_update(self, serializer):
        require_owner(self.request.user, serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        require_owner(self.request.user, instance)
        instance.delete()

    @action(detail=True, methods=['post'])
    def invitar(self, request, pk=None):
        proyecto = self.get_object()
        require_owner(request.user, proyecto)

        email = request.data.get('email')
        username = request.data.get('username')
        user_id = request.data.get('usuario_id')
        rol = request.data.get('rol', ProyectoMiembro.Rol.EDITOR)
        if not any((email, username, user_id)):
            raise ValidationError('Indica email, username o usuario_id para invitar.')
        if rol not in ProyectoMiembro.Rol.values or rol == ProyectoMiembro.Rol.PROPIETARIO:
            raise ValidationError({'rol': 'El rol debe ser arquitecto, editor o lector.'})

        usuarios = User.objects.all()
        if email:
            usuarios = usuarios.filter(email__iexact=email)
        elif username:
            usuarios = usuarios.filter(username__iexact=username)
        else:
            usuarios = usuarios.filter(pk=user_id)
        invitado = usuarios.first()
        if invitado is None:
            raise ValidationError({'usuario': 'No existe un usuario registrado con esos datos.'})
        if invitado.pk == proyecto.creador_id:
            raise ValidationError({'usuario': 'El propietario ya pertenece al proyecto.'})

        ProyectoMiembro.objects.update_or_create(
            proyecto=proyecto, usuario=invitado, defaults={'rol': rol}
        )
        return Response(ProyectoSerializer(proyecto).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['patch'], url_path=r'miembros/(?P<usuario_id>[^/.]+)')
    def cambiar_rol(self, request, pk=None, usuario_id=None):
        proyecto = self.get_object()
        require_owner(request.user, proyecto)
        rol = request.data.get('rol')
        if rol not in ProyectoMiembro.Rol.values or rol == ProyectoMiembro.Rol.PROPIETARIO:
            raise ValidationError({'rol': 'El rol debe ser arquitecto, editor o lector.'})
        try:
            miembro = proyecto.miembros.get(usuario_id=usuario_id)
        except ProyectoMiembro.DoesNotExist:
            raise ValidationError({'usuario': 'Ese usuario no es miembro del proyecto.'})
        if miembro.rol == ProyectoMiembro.Rol.PROPIETARIO:
            raise ValidationError({'usuario': 'No se puede cambiar el rol del propietario.'})
        miembro.rol = rol
        miembro.save(update_fields=['rol'])
        return Response(ProyectoSerializer(proyecto).data)

    @action(detail=True, methods=['delete'], url_path=r'colaboradores/(?P<usuario_id>[^/.]+)')
    def quitar_colaborador(self, request, pk=None, usuario_id=None):
        proyecto = self.get_object()
        require_owner(request.user, proyecto)
        deleted, _ = proyecto.miembros.exclude(
            rol=ProyectoMiembro.Rol.PROPIETARIO
        ).filter(usuario_id=usuario_id).delete()
        if not deleted:
            raise ValidationError({'usuario': 'Ese usuario no es miembro del proyecto.'})
        return Response(status=status.HTTP_204_NO_CONTENT)
