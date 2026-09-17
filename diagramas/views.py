from django.db.models import Q
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from .models import Diagrama, ClaseUML, AtributoUML, RelacionUML, VersionDiagrama
from .serializers import (
    DiagramaSerializer, ClaseUMLSerializer, AtributoUMLSerializer,
    RelacionUMLSerializer, VersionDiagramaSerializer
)

class DiagramAccessMixin:
    def allowed_diagrams(self):
        user = self.request.user
        return Diagrama.objects.filter(
            Q(proyecto__creador=user) | Q(proyecto__colaboradores=user)
        ).distinct()

    def ensure_allowed_diagram(self, diagrama):
        if not self.allowed_diagrams().filter(pk=diagrama.pk).exists():
            raise PermissionDenied("No tienes permiso para acceder a este lienzo.")


class DiagramaViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = DiagramaSerializer

    def get_queryset(self):
        queryset = self.allowed_diagrams()
        proyecto_id = self.request.query_params.get('proyecto')

        # El editor carga un proyecto a la vez. Aplicar el filtro en el
        # servidor evita que diagramas de otros proyectos accesibles se
        # mezclen al reabrir uno existente.
        if proyecto_id:
            queryset = queryset.filter(proyecto_id=proyecto_id)

        return queryset

    def perform_create(self, serializer):
        project = serializer.validated_data['proyecto']
        if not (project.creador == self.request.user or project.colaboradores.filter(pk=self.request.user.pk).exists()):
            raise PermissionDenied("No tienes permiso para usar este proyecto.")
        serializer.save()

    def perform_update(self, serializer):
        project = serializer.validated_data.get('proyecto', serializer.instance.proyecto)
        if not (project.creador == self.request.user or project.colaboradores.filter(pk=self.request.user.pk).exists()):
            raise PermissionDenied("No tienes permiso para usar este proyecto.")
        serializer.save()

class ClaseUMLViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = ClaseUMLSerializer

    def get_queryset(self):
        return ClaseUML.objects.filter(diagrama__in=self.allowed_diagrams())

    def perform_create(self, serializer):
        self.ensure_allowed_diagram(serializer.validated_data['diagrama'])
        serializer.save()

    def perform_update(self, serializer):
        self.ensure_allowed_diagram(serializer.validated_data.get('diagrama', serializer.instance.diagrama))
        serializer.save()

class AtributoUMLViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = AtributoUMLSerializer

    def get_queryset(self):
        return AtributoUML.objects.filter(clase__diagrama__in=self.allowed_diagrams())

    def perform_create(self, serializer):
        self.ensure_allowed_diagram(serializer.validated_data['clase'].diagrama)
        serializer.save()

    def perform_update(self, serializer):
        clase = serializer.validated_data.get('clase', serializer.instance.clase)
        self.ensure_allowed_diagram(clase.diagrama)
        serializer.save()

class RelacionUMLViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = RelacionUMLSerializer

    def get_queryset(self):
        return RelacionUML.objects.filter(diagrama__in=self.allowed_diagrams())

    def perform_create(self, serializer):
        self.ensure_allowed_diagram(serializer.validated_data['diagrama'])
        serializer.save()

    def perform_update(self, serializer):
        self.ensure_allowed_diagram(serializer.validated_data.get('diagrama', serializer.instance.diagrama))
        serializer.save()

class VersionDiagramaViewSet(DiagramAccessMixin, viewsets.ModelViewSet):
    serializer_class = VersionDiagramaSerializer

    def get_queryset(self):
        return VersionDiagrama.objects.filter(diagrama__in=self.allowed_diagrams())

    def perform_create(self, serializer):
        self.ensure_allowed_diagram(serializer.validated_data['diagrama'])
        serializer.save(usuario=self.request.user)
