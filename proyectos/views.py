from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework import status
from .models import Proyecto
from .serializers import ProyectoSerializer, UserSerializer
from django.contrib.auth.models import User
from .services import create_project_with_main_diagram, get_or_create_personal_workspace

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

class ProyectoViewSet(viewsets.ModelViewSet):
    serializer_class = ProyectoSerializer

    def get_queryset(self):
        user = self.request.user
        # También cubre cuentas existentes que fueron creadas antes de este
        # flujo y todavía no poseen su lienzo personal.
        get_or_create_personal_workspace(user)
        return Proyecto.objects.filter(
            Q(creador=user) | Q(colaboradores=user)
        ).distinct()

    def perform_create(self, serializer):
        # Un proyecto siempre nace con un lienzo compartible. Aún no hay nadie
        # más en él hasta que el creador invite colaboradores.
        proyecto = create_project_with_main_diagram(
            creator=self.request.user,
            nombre=serializer.validated_data['nombre'],
        )
        serializer.instance = proyecto

    def _ensure_owner(self, proyecto):
        if proyecto.creador_id != self.request.user.id:
            raise PermissionDenied('Solo el creador del proyecto puede administrar colaboradores.')

    def perform_update(self, serializer):
        self._ensure_owner(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._ensure_owner(instance)
        instance.delete()

    @action(detail=True, methods=['post'])
    def invitar(self, request, pk=None):
        """Agrega a un usuario registrado al proyecto y a sus diagramas."""
        proyecto = self.get_object()
        self._ensure_owner(proyecto)

        email = request.data.get('email')
        username = request.data.get('username')
        user_id = request.data.get('usuario_id')
        if not any((email, username, user_id)):
            raise ValidationError('Indica email, username o usuario_id para invitar.')

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
            raise ValidationError({'usuario': 'El creador ya pertenece al proyecto.'})

        proyecto.colaboradores.add(invitado)
        return Response(ProyectoSerializer(proyecto, context={'request': request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['delete'], url_path=r'colaboradores/(?P<usuario_id>[^/.]+)')
    def quitar_colaborador(self, request, pk=None, usuario_id=None):
        proyecto = self.get_object()
        self._ensure_owner(proyecto)
        if not proyecto.colaboradores.filter(pk=usuario_id).exists():
            raise ValidationError({'usuario': 'Ese usuario no es colaborador del proyecto.'})
        proyecto.colaboradores.remove(usuario_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
