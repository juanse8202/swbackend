from rest_framework import viewsets

from proyectos.models import ProyectoMiembro
from proyectos.permissions import EDIT_ROLES, require_role, role_for
from .models import AtributoUML, ClaseUML, Diagrama, RelacionUML, VersionDiagrama
from .serializers import (
    AtributoUMLSerializer, ClaseUMLSerializer, DiagramaSerializer,
    RelacionUMLSerializer, VersionDiagramaSerializer,
)


class DiagramAccessMixin:
    def allowed_diagrams(self):
        return Diagrama.objects.filter(
            proyecto__miembros__usuario=self.request.user
        ).distinct()

    def require_diagram_role(self, diagrama, roles, message='No tienes permiso para editar este lienzo.'):
        require_role(self.request.user, diagrama.proyecto, roles, message)


class DiagramaViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = DiagramaSerializer

    def get_queryset(self):
        queryset = self.allowed_diagrams()
        proyecto_id = self.request.query_params.get('proyecto')
        if proyecto_id:
            queryset = queryset.filter(proyecto_id=proyecto_id)
        return queryset.order_by('fecha_creacion', 'id')

    def perform_create(self, serializer):
        require_role(
            self.request.user, serializer.validated_data['proyecto'],
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
        )
        serializer.save()

    def perform_update(self, serializer):
        self.require_diagram_role(serializer.instance, EDIT_ROLES)
        if (
            role_for(self.request.user, serializer.instance.proyecto)
            == ProyectoMiembro.Rol.EDITOR
            and 'edges' in serializer.validated_data
            and serializer.validated_data['edges'] != serializer.instance.edges
        ):
            require_role(
                self.request.user,
                serializer.instance.proyecto,
                {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
                'Solo un arquitecto puede modificar relaciones.',
            )
        if (
            serializer.instance.proyecto_id != serializer.validated_data.get(
                'proyecto', serializer.instance.proyecto
            ).id
        ):
            self.require_diagram_role(
                serializer.instance,
                {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
            )
            require_role(
                self.request.user, serializer.validated_data['proyecto'],
                {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
            )
        serializer.save()

    def perform_destroy(self, instance):
        self.require_diagram_role(
            instance,
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
        )
        instance.delete()


class ClaseUMLViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = ClaseUMLSerializer

    def get_queryset(self):
        return ClaseUML.objects.filter(diagrama__in=self.allowed_diagrams())

    def perform_create(self, serializer):
        self.require_diagram_role(serializer.validated_data['diagrama'], EDIT_ROLES)
        serializer.save()

    def perform_update(self, serializer):
        self.require_diagram_role(serializer.instance.diagrama, EDIT_ROLES)
        serializer.save()

    def perform_destroy(self, instance):
        self.require_diagram_role(instance.diagrama, EDIT_ROLES)
        instance.delete()


class AtributoUMLViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = AtributoUMLSerializer

    def get_queryset(self):
        return AtributoUML.objects.filter(clase__diagrama__in=self.allowed_diagrams())

    def perform_create(self, serializer):
        self.require_diagram_role(serializer.validated_data['clase'].diagrama, EDIT_ROLES)
        serializer.save()

    def perform_update(self, serializer):
        self.require_diagram_role(serializer.instance.clase.diagrama, EDIT_ROLES)
        serializer.save()

    def perform_destroy(self, instance):
        self.require_diagram_role(instance.clase.diagrama, EDIT_ROLES)
        instance.delete()


class RelacionUMLViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = RelacionUMLSerializer

    def get_queryset(self):
        return RelacionUML.objects.filter(diagrama__in=self.allowed_diagrams())

    def perform_create(self, serializer):
        self.require_diagram_role(
            serializer.validated_data['diagrama'],
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
        )
        serializer.save()

    def perform_update(self, serializer):
        self.require_diagram_role(
            serializer.instance.diagrama,
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
        )
        serializer.save()

    def perform_destroy(self, instance):
        self.require_diagram_role(
            instance.diagrama,
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
        )
        instance.delete()


class VersionDiagramaViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = VersionDiagramaSerializer

    def get_queryset(self):
        return VersionDiagrama.objects.filter(diagrama__in=self.allowed_diagrams())

    def perform_create(self, serializer):
        self.require_diagram_role(serializer.validated_data['diagrama'], EDIT_ROLES)
        serializer.save(usuario=self.request.user)

    def perform_update(self, serializer):
        self.require_diagram_role(
            serializer.instance.diagrama,
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
        )
        serializer.save()

    def perform_destroy(self, instance):
        self.require_diagram_role(
            instance.diagrama,
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
        )
        instance.delete()
