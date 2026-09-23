from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from proyectos.models import ProyectoMiembro
from proyectos.permissions import EDIT_ROLES, require_role, role_for
from .models import AtributoUML, ClaseUML, Diagrama, RelacionUML, VersionDiagrama
from .serializers import (
    AtributoUMLSerializer, ClaseUMLSerializer, DiagramaSerializer,
    RelacionUMLSerializer, VersionDiagramaSerializer,
)
from .spring_generator import DiagramGenerationError, generate_spring_boot_zip
from .xmi import XmiError, export_xmi, parse_xmi


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
        # Para mutaciones se resuelve primero el objeto y luego se aplica
        # require_diagram_role: un colaborador expulsado recibe 403 en vez de
        # un 404 ambiguo. Las lecturas ajenas siguen ocultándose con 404.
        queryset = (
            Diagrama.objects.all()
            if self.action in {'update', 'partial_update', 'destroy'}
            else self.allowed_diagrams()
        )
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

    @action(detail=True, methods=['post'], url_path='generar-spring-boot')
    def generar_spring_boot(self, request, pk=None):
        """Genera un artefacto descargable, sin ejecutar código de usuario."""
        diagram = self.get_object()
        self.require_diagram_role(
            diagram,
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
            'Solo el propietario o arquitecto puede generar Spring Boot.',
        )
        options = request.data if isinstance(request.data, dict) else {}
        try:
            archive, filename = generate_spring_boot_zip(
                diagram.nodes,
                diagram.edges,
                artifact=options.get('artifact', diagram.nombre),
                package=options.get('package', 'com.diagramcraft.generated'),
            )
        except DiagramGenerationError as error:
            raise ValidationError({'errors': error.errors}) from error
        response = HttpResponse(archive, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    @action(detail=True, methods=['post'], url_path='importar-xmi')
    def importar_xmi(self, request, pk=None):
        diagram = self.get_object()
        self.require_diagram_role(
            diagram, {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
            'Solo el propietario o arquitecto puede importar XMI.',
        )
        upload = request.FILES.get('file')
        if upload is None:
            raise ValidationError({'file': 'Selecciona un archivo XMI.'})
        if not str(upload.name).lower().endswith(('.xmi', '.xml')):
            raise ValidationError({'file': 'El archivo debe terminar en .xmi o .xml.'})
        if request.data.get('mode', 'replace') != 'replace':
            raise ValidationError({'mode': 'Por ahora solo se admite el modo replace.'})
        try:
            imported = parse_xmi(upload.read())
        except XmiError as error:
            raise ValidationError({'errors': [error.error]}) from error
        # Validate the complete candidate before changing the persisted canvas.
        serializer = self.get_serializer(
            diagram, data={'nodes': imported['nodes'], 'edges': imported['edges']}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return HttpResponse(
            __import__('json').dumps({
                'nodes': serializer.instance.nodes, 'edges': serializer.instance.edges,
                'report': imported['report'],
            }), content_type='application/json', status=200,
        )

    @action(detail=True, methods=['get'], url_path='exportar-xmi')
    def exportar_xmi(self, request, pk=None):
        diagram = self.get_object()
        self.require_diagram_role(
            diagram, {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
            'Solo el propietario o arquitecto puede exportar XMI.',
        )
        serializer = self.get_serializer(diagram, data={'nodes': diagram.nodes, 'edges': diagram.edges}, partial=True)
        serializer.is_valid(raise_exception=True)
        xml = export_xmi(diagram.nodes, diagram.edges)
        response = HttpResponse(xml, content_type='application/xml; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="diagrama-{diagram.id}.xmi"'
        return response


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
