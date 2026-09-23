"""Deterministic, provider-free operations for the DiagramCraft AI agent.

This module deliberately has no ORM, HTTP, provider, or persistence calls.
Future AI providers may propose only the operations defined here; Django still
validates the resulting React Flow document before any write is authorised.
"""

from copy import deepcopy
from math import isfinite

from rest_framework import serializers

from .serializers import DiagramaSerializer


class OperationError(ValueError):
    """A rejected AI operation with its zero-based batch index."""

    def __init__(self, index, message):
        self.index = index
        self.message = message
        super().__init__(f'Operación {index}: {message}')


NODE_KINDS = {'class', 'entity', 'dto', 'enum', 'embeddable', 'interface'}
RELATION_TYPES = {
    'asociacion', 'agregacion', 'composicion', 'herencia',
    'realizacion', 'dependencia',
}
ATTRIBUTE_FIELDS = {'name', 'type', 'visibility', 'id', 'embedded', 'persistent'}
RELATION_DATA_FIELDS = {
    'relationType', 'multiplicidadOrigen', 'multiplicidadDestino',
    'relationConvention', 'wholeNodeId', 'ownerNodeId', 'jpaAnnotation',
    'jpaManaged', 'umlLabel', 'sourceRole', 'targetRole', 'bidirectional',
    'associationClassNodeId',
}
OPERATION_FIELDS = {
    'create_node': {'op', 'temp_id', 'kind', 'title', 'position', 'properties', 'methods', 'abstract'},
    'update_node': {'op', 'node_id', 'patch'},
    'move_node': {'op', 'node_id', 'position'},
    'delete_node': {'op', 'node_id', 'cascade_incident'},
    'add_attribute': {'op', 'node_id', 'attribute'},
    'update_attribute': {'op', 'node_id', 'attribute_name', 'attribute'},
    'remove_attribute': {'op', 'node_id', 'attribute_name'},
    'create_relation': {'op', 'source', 'target', 'relation_type', 'data'},
    'update_relation': {'op', 'edge_id', 'source', 'target', 'relation_type', 'data'},
    'delete_relation': {'op', 'edge_id'},
}


def _reject(index, message):
    raise OperationError(index, message)


def _strict_object(value, allowed, index, label, required=()):
    if not isinstance(value, dict):
        _reject(index, f'{label} debe ser un objeto.')
    unknown = set(value) - set(allowed)
    if unknown:
        _reject(index, f'{label} contiene claves no permitidas: {", ".join(sorted(unknown))}.')
    missing = set(required) - set(value)
    if missing:
        _reject(index, f'{label} requiere: {", ".join(sorted(missing))}.')
    return value


def _nonempty_text(value, index, field, limit=160):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        _reject(index, f'{field} debe ser texto no vacío de hasta {limit} caracteres.')
    return value.strip()


def _position(value, index):
    value = _strict_object(value, {'x', 'y'}, index, 'position', required={'x', 'y'})
    result = {}
    for axis in ('x', 'y'):
        number = value[axis]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not isfinite(number):
            _reject(index, f'position.{axis} debe ser un número finito.')
        result[axis] = number
    return result


def _node_id(nodes):
    existing = {node.get('id') for node in nodes}
    number = len(nodes) + 1
    while f'ai-node-{number}' in existing:
        number += 1
    return f'ai-node-{number}'


def _edge_id(edges):
    existing = {edge.get('id') for edge in edges}
    number = len(edges) + 1
    while f'ai-relation-{number}' in existing:
        number += 1
    return f'ai-relation-{number}'


def _lookup(items, item_id, index, kind):
    if not isinstance(item_id, str) or not item_id:
        _reject(index, f'{kind}_id debe ser texto no vacío.')
    item = next((value for value in items if value.get('id') == item_id), None)
    if item is None:
        _reject(index, f'No existe {kind} {item_id!r}.')
    return item


def _attribute(value, index):
    value = _strict_object(value, ATTRIBUTE_FIELDS, index, 'attribute', required={'name', 'type'})
    attribute = {'name': _nonempty_text(value['name'], index, 'attribute.name'),
                 'type': _nonempty_text(value['type'], index, 'attribute.type')}
    for key in ATTRIBUTE_FIELDS - {'name', 'type'}:
        if key in value:
            attribute[key] = value[key]
    return attribute


def _resolve(value, references, index, field):
    if not isinstance(value, str) or not value:
        _reject(index, f'{field} debe ser texto no vacío.')
    return references.get(value, value)


def _validate_document(nodes, edges, index):
    try:
        DiagramaSerializer().validate({'nodes': nodes, 'edges': edges})
    except serializers.ValidationError as error:
        _reject(index, f'El resultado viola las reglas UML: {error.detail}')


def execute_operations(nodes, edges, operations):
    """Apply a strict operation batch to copies and return ``{'nodes','edges'}``.

    The caller owns persistence.  Any rejected operation raises before a
    candidate is returned, so the input objects remain untouched.
    """
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise OperationError(-1, 'nodes y edges deben ser listas.')
    if not isinstance(operations, list) or not operations or len(operations) > 20:
        raise OperationError(-1, 'operations debe contener entre 1 y 20 operaciones.')

    candidate_nodes, candidate_edges = deepcopy(nodes), deepcopy(edges)
    references = {}
    for index, operation in enumerate(operations):
        operation = _strict_object(
            operation,
            {
                'op', 'temp_id', 'kind', 'title', 'position', 'properties', 'methods', 'abstract',
                'node_id', 'patch', 'attribute', 'attribute_name', 'source', 'target',
                'relation_type', 'data', 'edge_id', 'cascade_incident',
            },
            index,
            'operation',
            required={'op'},
        )
        op = operation['op']
        allowed_fields = OPERATION_FIELDS.get(op)
        if allowed_fields is None:
            _reject(index, f'op no soportada: {op!r}.')
        unknown = set(operation) - allowed_fields
        if unknown:
            _reject(index, f'{op} contiene claves no permitidas: {", ".join(sorted(unknown))}.')
        if op == 'create_node':
            required = {'kind', 'title'}
            if not required <= set(operation):
                _reject(index, 'create_node requiere kind y title.')
            kind = operation['kind']
            if kind not in NODE_KINDS:
                _reject(index, 'kind no soportado.')
            properties = operation.get('properties', [])
            if not isinstance(properties, list):
                _reject(index, 'properties debe ser una lista.')
            if kind in {'interface', 'enum'} and properties:
                _reject(index, f'Un nodo {kind} no puede crearse con atributos.')
            normalized_properties = [_attribute(item, index) for item in properties]
            if kind == 'entity' and not any('(@Id)' in item['type'] for item in normalized_properties):
                normalized_properties.insert(0, {'name': 'id', 'type': 'UUID (@Id)', 'visibility': 'private'})
            node_id = _node_id(candidate_nodes)
            title = _nonempty_text(operation['title'], index, 'title')
            if kind != 'enum' and not title.endswith('.java'):
                title = f'{title}.java'
            data = {'kind': kind, 'title': title, 'properties': normalized_properties,
                    'methods': deepcopy(operation.get('methods', []))}
            if 'abstract' in operation:
                if not isinstance(operation['abstract'], bool):
                    _reject(index, 'abstract debe ser verdadero o falso.')
                data['abstract'] = operation['abstract']
            candidate_nodes.append({
                'id': node_id, 'type': 'umlClass',
                'position': _position(operation.get('position', {'x': 120 + len(candidate_nodes) * 40, 'y': 120}), index),
                'data': data,
            })
            temp_id = operation.get('temp_id')
            if temp_id is not None:
                temp_id = _nonempty_text(temp_id, index, 'temp_id')
                if temp_id in references:
                    _reject(index, f'temp_id duplicado: {temp_id}.')
                references[temp_id] = node_id
        elif op == 'update_node':
            node = _lookup(candidate_nodes, _resolve(operation.get('node_id'), references, index, 'node_id'), index, 'node')
            patch = _strict_object(operation.get('patch'), {'title', 'kind', 'abstract'}, index, 'patch')
            if not patch:
                _reject(index, 'patch no puede estar vacío.')
            if 'title' in patch:
                title = _nonempty_text(patch['title'], index, 'patch.title')
                node['data']['title'] = title if title.endswith('.java') else f'{title}.java'
            if 'kind' in patch:
                if patch['kind'] not in NODE_KINDS:
                    _reject(index, 'patch.kind no soportado.')
                node['data']['kind'] = patch['kind']
            if 'abstract' in patch:
                if not isinstance(patch['abstract'], bool):
                    _reject(index, 'patch.abstract debe ser verdadero o falso.')
                node['data']['abstract'] = patch['abstract']
        elif op == 'move_node':
            node = _lookup(candidate_nodes, _resolve(operation.get('node_id'), references, index, 'node_id'), index, 'node')
            node['position'] = _position(operation.get('position'), index)
        elif op == 'delete_node':
            node_id = _resolve(operation.get('node_id'), references, index, 'node_id')
            _lookup(candidate_nodes, node_id, index, 'node')
            incident = [edge for edge in candidate_edges if node_id in {edge.get('source'), edge.get('target')}]
            if incident and operation.get('cascade_incident') is not True:
                _reject(index, 'delete_node con relaciones requiere cascade_incident=true.')
            candidate_nodes = [node for node in candidate_nodes if node.get('id') != node_id]
            candidate_edges = [edge for edge in candidate_edges if edge not in incident]
        elif op in {'add_attribute', 'update_attribute', 'remove_attribute'}:
            node = _lookup(candidate_nodes, _resolve(operation.get('node_id'), references, index, 'node_id'), index, 'node')
            if node.get('data', {}).get('kind') in {'interface', 'enum'}:
                _reject(index, 'No se permiten atributos en interface o enum.')
            attributes = node.setdefault('data', {}).setdefault('properties', [])
            name = operation.get('attribute_name')
            if op == 'add_attribute':
                attribute = _attribute(operation.get('attribute'), index)
                if any(item.get('name') == attribute['name'] for item in attributes):
                    _reject(index, f'Ya existe el atributo {attribute["name"]!r}.')
                attributes.append(attribute)
            else:
                name = _nonempty_text(name, index, 'attribute_name')
                attribute = next((item for item in attributes if item.get('name') == name), None)
                if attribute is None:
                    _reject(index, f'No existe el atributo {name!r}.')
                if op == 'remove_attribute':
                    attributes.remove(attribute)
                else:
                    patch = _attribute(operation.get('attribute'), index)
                    if patch['name'] != name and any(item.get('name') == patch['name'] for item in attributes):
                        _reject(index, f'Ya existe el atributo {patch["name"]!r}.')
                    attribute.clear(); attribute.update(patch)
        elif op == 'create_relation':
            source = _resolve(operation.get('source'), references, index, 'source')
            target = _resolve(operation.get('target'), references, index, 'target')
            _lookup(candidate_nodes, source, index, 'node'); _lookup(candidate_nodes, target, index, 'node')
            relation_type = operation.get('relation_type')
            if relation_type not in RELATION_TYPES:
                _reject(index, 'relation_type no soportado.')
            data = _strict_object(operation.get('data', {}), RELATION_DATA_FIELDS, index, 'data')
            data = deepcopy(data); data['relationType'] = relation_type
            for reference_field in ('wholeNodeId', 'ownerNodeId', 'associationClassNodeId'):
                if reference_field in data:
                    data[reference_field] = _resolve(data[reference_field], references, index, reference_field)
            candidate_edges.append({'id': _edge_id(candidate_edges), 'source': source, 'target': target,
                                    'type': 'relationEdge', 'data': data})
        elif op == 'update_relation':
            edge = _lookup(candidate_edges, operation.get('edge_id'), index, 'relation')
            if 'source' in operation:
                edge['source'] = _resolve(operation['source'], references, index, 'source')
            if 'target' in operation:
                edge['target'] = _resolve(operation['target'], references, index, 'target')
            for endpoint in (edge['source'], edge['target']):
                _lookup(candidate_nodes, endpoint, index, 'node')
            if 'relation_type' in operation:
                if operation['relation_type'] not in RELATION_TYPES:
                    _reject(index, 'relation_type no soportado.')
                edge.setdefault('data', {})['relationType'] = operation['relation_type']
            if 'data' in operation:
                patch = _strict_object(operation['data'], RELATION_DATA_FIELDS, index, 'data')
                patch = deepcopy(patch)
                for reference_field in ('wholeNodeId', 'ownerNodeId', 'associationClassNodeId'):
                    if reference_field in patch:
                        patch[reference_field] = _resolve(
                            patch[reference_field], references, index, reference_field
                        )
                edge.setdefault('data', {}).update(patch)
        elif op == 'delete_relation':
            edge = _lookup(candidate_edges, operation.get('edge_id'), index, 'relation')
            candidate_edges.remove(edge)
        else:
            _reject(index, f'op no soportada: {op!r}.')
        _validate_document(candidate_nodes, candidate_edges, index)
    return {'nodes': candidate_nodes, 'edges': candidate_edges}
