import json

from django.db import transaction
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from proyectos.models import ProyectoMiembro
from proyectos.permissions import EDIT_ROLES, require_role, role_for
from .models import AtributoUML, ClaseUML, Diagrama, RelacionUML, VersionDiagrama
from .serializers import (
    AtributoUMLSerializer, ClaseUMLSerializer, DiagramaSerializer,
    RelacionUMLSerializer, VersionDiagramaSerializer,
)
from .spring_generator import DiagramGenerationError, generate_spring_boot_zip
from .xmi import XmiError, export_xmi, parse_xmi
from .ai_interpreter import (
    AiInterpretationError, AiProviderError, build_diagram_summary, class_create_proposal, get_ai_provider,
    project_create_proposal, validate_interpret_request, validate_provider_response,
)
from .ai_operations import OperationError, execute_operations
from .ai_plans import PlanError, apply_plan, create_plan
from .api_errors import DiagramApiError, stale_revision_error
from .realtime import publish_diagram_update


def destructive_impact(nodes, edges, operations):
    """Describe existing elements a proposed delete would remove for the preview."""
    node_ids = set()
    edge_ids = set()
    for operation in operations:
        if operation.get('op') == 'delete_node':
            node_id = operation.get('node_id')
            if isinstance(node_id, str):
                node_ids.add(node_id)
        elif operation.get('op') == 'delete_relation':
            edge_id = operation.get('edge_id')
            if isinstance(edge_id, str):
                edge_ids.add(edge_id)
    edge_ids.update(
        edge.get('id') for edge in edges
        if edge.get('source') in node_ids or edge.get('target') in node_ids
    )
    return {
        'nodes': [node_id for node_id in node_ids if any(node.get('id') == node_id for node in nodes)],
        'edges': [edge_id for edge_id in edge_ids if isinstance(edge_id, str)],
    }


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
        original_id = serializer.instance.pk
        with transaction.atomic():
            locked = Diagrama.objects.select_for_update().get(pk=original_id)
            serializer.instance = locked
            self.require_diagram_role(locked, EDIT_ROLES)
            expected_revision = serializer.validated_data.pop('expected_revision', None)
            document_submitted = any(field in serializer.validated_data for field in ('nodes', 'edges'))
            document_changed = any(
                field in serializer.validated_data and serializer.validated_data[field] != getattr(locked, field)
                for field in ('nodes', 'edges')
            )
            if document_submitted and expected_revision is None:
                raise DiagramApiError(
                    'expected_revision es obligatorio al guardar nodes o edges.',
                    code='expected_revision_required', details={'field': 'expected_revision'},
                )
            if document_submitted and expected_revision != locked.revision:
                raise stale_revision_error(locked.revision)
            if (
                role_for(self.request.user, locked.proyecto)
                == ProyectoMiembro.Rol.EDITOR
                and 'edges' in serializer.validated_data
                and serializer.validated_data['edges'] != locked.edges
            ):
                require_role(
                    self.request.user,
                    locked.proyecto,
                    {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
                    'Solo un arquitecto puede modificar relaciones.',
                )
            if (
                locked.proyecto_id != serializer.validated_data.get('proyecto', locked.proyecto).id
            ):
                self.require_diagram_role(
                    locked,
                    {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
                )
                require_role(
                    self.request.user, serializer.validated_data['proyecto'],
                    {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
                )
            revision = locked.revision + 1 if document_changed else locked.revision
            serializer.save(revision=revision)
            if document_changed:
                nodes, edges = serializer.instance.nodes, serializer.instance.edges
                transaction.on_commit(lambda: publish_diagram_update(
                    diagram_id=original_id, nodes=nodes, edges=edges, revision=revision,
                ))
        return

    def perform_destroy(self, instance):
        self.require_diagram_role(
            instance,
            {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
        )
        instance.delete()

    @action(detail=True, methods=['post'], url_path='interpretar-ia')
    def interpretar_ia(self, request, pk=None):
        """Interpret text into a validated, non-persisted modelling plan."""
        diagram = self.get_object()
        self.require_diagram_role(diagram, EDIT_ROLES)
        try:
            instruction, selection, expected_revision, _request_id = validate_interpret_request(
                request.data, diagram.nodes, diagram.edges
            )
            if expected_revision is not None and expected_revision != diagram.revision:
                raise stale_revision_error(diagram.revision)
            proposal = project_create_proposal(instruction) or class_create_proposal(instruction)
            if proposal is None:
                context = build_diagram_summary(diagram.nodes, diagram.edges, selection)
                context['instruction'] = instruction
                proposal = validate_provider_response(
                    get_ai_provider().interpret(context), diagram.nodes, diagram.edges
                )
            if proposal['status'] != 'ready':
                return Response({
                    'status': proposal['status'], 'summary': proposal['summary'],
                    'question': proposal['question'], 'candidates': proposal['candidates'],
                    'message': proposal['question'], 'operations': [], 'request_id': _request_id,
                })
            candidate = execute_operations(diagram.nodes, diagram.edges, proposal['operations'])
            if candidate['edges'] != diagram.edges:
                self.require_diagram_role(
                    diagram,
                    {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
                    'Solo un arquitecto puede proponer cambios de relaciones.',
                )
        except DiagramApiError as error:
            return Response(error.detail, status=error.status_code)
        except (AiInterpretationError, AiProviderError, OperationError) as error:
            return Response({
                'code': 'ai_interpretation_failed', 'message': str(error),
                'target': None, 'current_revision': diagram.revision,
            }, status=400)

        # Gemini has finished before this write; a plan is bound to the
        # current revision and cannot be replaced by client-provided operations.
        diagram.refresh_from_db(fields=['revision'])
        plan = create_plan(user=request.user, diagram=diagram, operations=proposal['operations'])
        return Response({
            'status': 'ready', 'plan_id': str(plan.plan_id), 'base_revision': plan.revision_base,
            'summary': proposal['summary'],
            'operations': proposal['operations'],
            'requires_confirmation': any(
                operation.get('op') in {
                    'create_node', 'add_attribute', 'add_method', 'create_relation',
                    'delete_node', 'delete_relation', 'project.create',
                }
                for operation in proposal['operations']
            ),
            'destructive_impact': destructive_impact(
                diagram.nodes, diagram.edges, proposal['operations'],
            ),
            'request_id': _request_id,
        })

    @action(detail=True, methods=['post'], url_path='aplicar-plan-ia')
    def aplicar_plan_ia(self, request, pk=None):
        """Commit a stored plan; the request can never supply operations."""
        # Access filtering happens in get_object. apply_plan locks and checks
        # membership again inside the transaction before any write.
        self.get_object()
        try:
            result = apply_plan(user=request.user, diagram_id=pk, payload=request.data)
        except PlanError as error:
            return Response(error.detail, status=error.status_code)
        if result.get('status') == 'applied':
            publish_diagram_update(
                diagram_id=pk, nodes=result['nodes'], edges=result['edges'],
                revision=result['revision'], origin_request_id=request.data.get('idempotency_key'),
            )
            created = result.get('diagram')
            if created:
                publish_diagram_update(
                    diagram_id=created['id'], nodes=created['nodes'], edges=created['edges'],
                    revision=created['revision'], origin_request_id=request.data.get('idempotency_key'),
                )
        return Response(result)

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
            archive, filename, warnings = generate_spring_boot_zip(
                diagram.nodes,
                diagram.edges,
                artifact=options.get('artifact', diagram.nombre),
                package=options.get('package', 'com.diagramcraft.generated'),
                return_warnings=True,
            )
        except DiagramGenerationError as error:
            raise ValidationError({'errors': error.errors}) from error
        response = HttpResponse(archive, content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        if warnings:
            response['X-DiagramCraft-Warnings'] = json.dumps(warnings, ensure_ascii=False)
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
        # Validate and replace under a row lock so a concurrent plan becomes
        # stale instead of silently overwriting an import.
        with transaction.atomic():
            diagram = Diagrama.objects.select_for_update().get(pk=diagram.pk)
            serializer = self.get_serializer(
                diagram, data={'nodes': imported['nodes'], 'edges': imported['edges']}, partial=True
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(revision=diagram.revision + 1)
            revision = serializer.instance.revision
            nodes, edges = serializer.instance.nodes, serializer.instance.edges
            transaction.on_commit(lambda: publish_diagram_update(
                diagram_id=diagram.pk, nodes=nodes, edges=edges, revision=revision,
            ))
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
        xml = export_xmi(diagram.nodes, diagram.edges, diagram_name=diagram.nombre)
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
