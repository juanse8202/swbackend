"""Authoritative creation and application of short-lived AI plans."""

from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from proyectos.models import ProyectoMiembro
from proyectos.permissions import EDIT_ROLES, require_role

from .ai_operations import OperationError, execute_operations
from .models import Diagrama, PlanIA


class PlanError(ValueError):
    """A plan could not be safely created or applied."""


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
    return any(operation.get('op') in {'delete_node', 'delete_relation'} for operation in operations)


def _validate_apply_payload(payload):
    if not isinstance(payload, dict):
        raise PlanError('El cuerpo debe ser un objeto JSON.')
    if set(payload) != {'plan_id', 'idempotency_key', 'confirm'}:
        raise PlanError('Solo se permiten plan_id, idempotency_key y confirm.')
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
    return plan_id, key, confirm


def apply_plan(*, user, diagram_id, payload):
    """Apply exactly one server-owned plan atomically and idempotently."""
    plan_id, idempotency_key, confirm = _validate_apply_payload(payload)
    with transaction.atomic():
        diagram = Diagrama.objects.select_for_update().select_related('proyecto').filter(pk=diagram_id).first()
        if diagram is None:
            raise PlanError('El diagrama no existe.')
        require_role(user, diagram.proyecto, EDIT_ROLES)
        plan = PlanIA.objects.select_for_update().filter(plan_id=plan_id, diagrama=diagram).first()
        if plan is None or plan.usuario_id != user.id:
            raise PlanError('El plan no existe para este usuario y diagrama.')
        if plan.estado == PlanIA.Estado.APLICADO:
            if plan.idempotency_key != idempotency_key:
                raise PlanError('El plan ya fue aplicado con otra clave de idempotencia.')
            return plan.resultado
        if plan.estado != PlanIA.Estado.PENDIENTE:
            raise PlanError('El plan ya no puede aplicarse.')
        if plan.expira_en <= timezone.now():
            plan.estado = PlanIA.Estado.EXPIRADO
            plan.save(update_fields=['estado'])
            raise PlanError('El plan vencio; vuelve a interpretarlo.')
        if plan.revision_base != diagram.revision:
            raise PlanError('El diagrama cambio desde que se creo el plan.')
        if _requires_confirmation(plan.operaciones) and not confirm:
            raise PlanError('Este plan requiere confirm=true por incluir un borrado.')
        if plan.idempotency_key and plan.idempotency_key != idempotency_key:
            raise PlanError('El plan ya esta asociado a otra clave de idempotencia.')
        try:
            candidate = execute_operations(diagram.nodes, diagram.edges, plan.operaciones)
        except OperationError as error:
            raise PlanError(f'El plan ya no es valido: {error}') from error
        if candidate['edges'] != diagram.edges:
            require_role(
                user, diagram.proyecto,
                {ProyectoMiembro.Rol.PROPIETARIO, ProyectoMiembro.Rol.ARQUITECTO},
                'Solo un arquitecto puede modificar relaciones.',
            )
        diagram.nodes = candidate['nodes']
        diagram.edges = candidate['edges']
        diagram.revision += 1
        diagram.save(update_fields=['nodes', 'edges', 'revision', 'fecha_modificacion'])
        result = {
            'status': 'applied', 'plan_id': str(plan.plan_id), 'revision': diagram.revision,
            'nodes': candidate['nodes'], 'edges': candidate['edges'],
        }
        plan.estado = PlanIA.Estado.APLICADO
        plan.idempotency_key = idempotency_key
        plan.resultado = result
        plan.fecha_aplicacion = timezone.now()
        plan.save(update_fields=['estado', 'idempotency_key', 'resultado', 'fecha_aplicacion'])
        return result
