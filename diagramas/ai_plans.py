"""Authoritative creation and application of short-lived AI plans."""

from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from django.db import IntegrityError

from proyectos.models import ProyectoMiembro
from proyectos.permissions import EDIT_ROLES, require_role
from proyectos.services import create_project_with_main_diagram

from .ai_operations import OperationError, execute_operations
from .models import Diagrama, PlanIA
from .api_errors import DiagramApiError


class PlanError(DiagramApiError):
    """A plan could not be safely created or applied."""

    def __init__(self, message, *, code='invalid_plan', target=None, details=None,
                 current_revision=None, status_code=400):
        super().__init__(
            message, code=code, target=target, details=details,
            current_revision=current_revision, status_code=status_code,
        )


def operations_hash(operations):
    payload = json.dumps(operations, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256(payload.encode('utf-8')).hexdigest()


def create_plan(*, user, diagram, operations):
    """Store only already-normalized operations from a validated proposal."""
    return PlanIA.objects.create(
        diagrama=diagram,
        usuario=user,
        operaciones=operations,
        revision_base=diagram.revision,
        request_hash=operations_hash(operations),
        expira_en=timezone.now() + timedelta(seconds=settings.AI_PLAN_TTL_SECONDS),
    )


def _requires_confirmation(operations):
    return any(operation.get('op') in {
        'create_node', 'add_attribute', 'add_method', 'create_relation',
        'delete_node', 'delete_relation', 'project.create',
    } for operation in operations)


def _operation_target(operations, index):
    """Expose the affected existing element when a typed operation is invalid."""
    if not isinstance(index, int) or index < 0 or index >= len(operations):
        return None
    operation = operations[index]
    if not isinstance(operation, dict):
        return None
    node_id = operation.get('node_id')
    if isinstance(node_id, str):
        return {'kind': 'node', 'id': node_id}
    edge_id = operation.get('edge_id')
    if isinstance(edge_id, str):
        return {'kind': 'edge', 'id': edge_id}
    return None


def _project_create_operations(operations):
    return [operation for operation in operations if operation.get('op') == 'project.create']


def _validate_apply_payload(payload):
    if not isinstance(payload, dict):
        raise PlanError('El cuerpo debe ser un objeto JSON.')
    allowed = {'plan_id', 'idempotency_key', 'confirm', 'expected_revision', 'request_id'}
    if not set(payload) <= allowed or not {'plan_id', 'idempotency_key', 'confirm'} <= set(payload):
        raise PlanError('Solo se permiten plan_id, idempotency_key, confirm, expected_revision y request_id.')
    plan_id = payload.get('plan_id')
    key = payload.get('idempotency_key')
    confirm = payload.get('confirm')
    if not isinstance(plan_id, str) or not plan_id:
        raise PlanError('plan_id debe ser texto no vacio.')
    try:
        UUID(plan_id)
    except (TypeError, ValueError) as error:
        raise PlanError('plan_id no tiene un formato valido.') from error
    if not isinstance(key, str) or not key or len(key) > 128:
        raise PlanError('idempotency_key debe ser texto no vacio de hasta 128 caracteres.')
    if not isinstance(confirm, bool):
        raise PlanError('confirm debe ser verdadero o falso.')
    expected_revision = payload.get('expected_revision')
    if expected_revision is not None and (
        isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0
    ):
        raise PlanError('expected_revision debe ser un entero no negativo.')
    request_id = payload.get('request_id')
    if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 128):
        raise PlanError('request_id debe ser texto no vacio de hasta 128 caracteres.')
    return plan_id, key, confirm, expected_revision


def apply_plan(*, user, diagram_id, payload):
    """Apply exactly one server-owned plan atomically and idempotently."""
    plan_id, idempotency_key, confirm, expected_revision = _validate_apply_payload(payload)
    with transaction.atomic():
        diagram = Diagrama.objects.select_for_update().select_related('proyecto').filter(pk=diagram_id).first()
        if diagram is None:
            raise PlanError('El diagrama no existe.')
        require_role(user, diagram.proyecto, EDIT_ROLES)
        if expected_revision is not None and expected_revision != diagram.revision:
            raise PlanError(
                'El diagrama cambio desde que se preparo la solicitud.', code='stale_revision',
                current_revision=diagram.revision, status_code=409,
            )
        plan = PlanIA.objects.select_for_update().filter(plan_id=plan_id, diagrama=diagram).first()
        if plan is None or plan.usuario_id != user.id:
            raise PlanError('El plan no existe para este usuario y diagrama.', code='plan_not_found')
        if plan.estado == PlanIA.Estado.APLICADO:
            if plan.idempotency_key != idempotency_key:
                raise PlanError('El plan ya fue aplicado con otra clave de idempotencia.', code='idempotency_conflict', status_code=409)
            return plan.resultado
        if plan.estado != PlanIA.Estado.PENDIENTE:
            raise PlanError('El plan ya no puede aplicarse.', code='plan_unavailable')
        if plan.expira_en <= timezone.now():
            plan.estado = PlanIA.Estado.EXPIRADO
            plan.save(update_fields=['estado'])
            raise PlanError('El plan vencio; vuelve a interpretarlo.', code='plan_expired')
        if plan.revision_base != diagram.revision:
            raise PlanError(
                'El diagrama cambio desde que se creo el plan.', code='stale_revision',
                current_revision=diagram.revision, status_code=409,
            )
        if _requires_confirmation(plan.operaciones) and not confirm:
            raise PlanError('Este plan requiere confirm=true antes de crear o eliminar elementos.', code='confirmation_required')
        if plan.idempotency_key and plan.idempotency_key != idempotency_key:
            raise PlanError('El plan ya esta asociado a otra clave de idempotencia.', code='idempotency_conflict', status_code=409)
        try:
            candidate = execute_operations(diagram.nodes, diagram.edges, plan.operaciones)
        except OperationError as error:
            raise PlanError(
                f'El plan ya no es valido: {error}', code='invalid_plan',
                target=_operation_target(plan.operaciones, error.index),
                details={'operation_index': error.index},
            ) from error
        project_operations = _project_create_operations(plan.operaciones)
        if len(project_operations) > 1:
            raise PlanError('Un plan solo puede crear un proyecto.', code='invalid_plan')
        if project_operations and len(plan.operaciones) != 1:
            raise PlanError(
                'project.create no puede mezclarse con cambios del diagrama.',
                code='invalid_plan', details={'operation_index': 0},
            )
        if candidate['edges'] != diagram.edges:
            require_role(
                user, diagram.proyecto,
                {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
                'Solo un arquitecto puede modificar relaciones.',
            )
        document_changed = candidate['nodes'] != diagram.nodes or candidate['edges'] != diagram.edges
        if document_changed:
            diagram.nodes = candidate['nodes']
            diagram.edges = candidate['edges']
            diagram.revision += 1
            diagram.save(update_fields=['nodes', 'edges', 'revision', 'fecha_modificacion'])
        result = {
            'status': 'applied', 'plan_id': str(plan.plan_id), 'revision': diagram.revision,
            'nodes': candidate['nodes'], 'edges': candidate['edges'],
        }
        if project_operations:
            payload = project_operations[0]['payload']
            try:
                project = create_project_with_main_diagram(creator=user, nombre=payload['name'])
            except IntegrityError as error:
                raise PlanError('Ya tienes un proyecto activo con ese nombre.', code='duplicate_project_name', status_code=409) from error
            created_diagram = project.diagramas.get(nombre='Diagrama principal')
            result['project'] = {'id': project.id, 'nombre': project.nombre}
            result['diagram'] = {
                'id': created_diagram.id, 'nodes': created_diagram.nodes,
                'edges': created_diagram.edges, 'revision': created_diagram.revision,
            }
            result['summary'] = f'Proyecto {project.nombre} creado.'
        plan.estado = PlanIA.Estado.APLICADO
        plan.idempotency_key = idempotency_key
        plan.resultado = result
        plan.fecha_aplicacion = timezone.now()
        plan.save(update_fields=['estado', 'idempotency_key', 'resultado', 'fecha_aplicacion'])
        return result
