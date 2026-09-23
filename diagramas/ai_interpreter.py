"""Provider boundary for phase 2 of the DiagramCraft modelling agent.

The provider receives an intentionally small, read-only summary and can only
propose JSON operations. It never receives a Django model or persistence
capability, and the view validates every proposal with ``execute_operations``.
"""

import json
import os

from django.conf import settings


class AiProviderError(RuntimeError):
    """A safe, user-facing category for an unavailable AI provider."""


class AiInterpretationError(ValueError):
    """An invalid, unsafe, or ambiguous request/response payload."""


class GeminiInterpreter:
    """Thin adapter around Google GenAI; imported lazily for test isolation."""

    def __init__(self, *, api_key=None, model=None, timeout_seconds=None):
        self.api_key = api_key or os.getenv('GEMINI_API_KEY', '')
        self.model = model or os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
        self.timeout_seconds = timeout_seconds or int(os.getenv('AI_REQUEST_TIMEOUT_SECONDS', '20'))

    def interpret(self, context):
        if not self.api_key:
            raise AiProviderError('El proveedor de IA no esta configurado.')
        try:
            from google import genai
            client = genai.Client(
                api_key=self.api_key,
                http_options={'timeout': int(self.timeout_seconds * 1000)},
            )
            response = client.interactions.create(
                model=self.model,
                input=_prompt(context),
                response_format={
                    'type': 'text',
                    'mime_type': 'application/json',
                    'schema': RESPONSE_SCHEMA,
                },
            )
            return json.loads(response.output_text)
        except (ImportError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise AiProviderError('El proveedor devolvio una respuesta no valida.') from error
        except Exception as error:  # SDK exceptions vary by transport/version.
            raise AiProviderError('No se pudo contactar al proveedor de IA.') from error


RESPONSE_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'status': {'type': 'string', 'enum': ['ready', 'clarification', 'unsupported']},
        'summary': {'type': 'string'},
        'question': {'type': 'string'},
        'candidates': {'type': 'array', 'items': {'type': 'string'}},
        'operations': {'type': 'array', 'items': {'type': 'object'}},
    },
    'required': ['status', 'summary', 'question', 'candidates', 'operations'],
}


def get_ai_provider():
    """Factory deliberately patchable with a fake in tests."""
    return GeminiInterpreter()


def build_diagram_summary(nodes, edges, selection):
    """Return only facts required to identify UML elements, never secrets."""
    return {
        'nodes': [
            {
                'id': node.get('id'),
                'kind': (node.get('data') or {}).get('kind'),
                'title': (node.get('data') or {}).get('title'),
                'position': node.get('position'),
                'attributes': [
                    {'name': item.get('name'), 'type': item.get('type')}
                    for item in (node.get('data') or {}).get('properties', [])
                    if isinstance(item, dict)
                ],
            }
            for node in nodes if isinstance(node, dict)
        ],
        'edges': [
            {
                'id': edge.get('id'), 'source': edge.get('source'), 'target': edge.get('target'),
                'relation_type': (edge.get('data') or {}).get('relationType'),
                'multiplicidad_origen': (edge.get('data') or {}).get('multiplicidadOrigen'),
                'multiplicidad_destino': (edge.get('data') or {}).get('multiplicidadDestino'),
                'whole_node_id': (edge.get('data') or {}).get('wholeNodeId'),
                'label': (edge.get('data') or {}).get('umlLabel'),
            }
            for edge in edges if isinstance(edge, dict)
        ],
        'selection': selection,
    }


def validate_interpret_request(payload, nodes, edges):
    """Strictly validate untrusted HTTP input before the remote call."""
    if not isinstance(payload, dict):
        raise AiInterpretationError('El cuerpo debe ser un objeto JSON.')
    unknown = set(payload) - {'instruction', 'selection'}
    if unknown:
        raise AiInterpretationError(f'Claves no permitidas: {", ".join(sorted(unknown))}.')
    instruction = payload.get('instruction')
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction.strip()) > 2000:
        raise AiInterpretationError('instruction debe ser texto no vacio de hasta 2000 caracteres.')
    selection = payload.get('selection', {})
    if not isinstance(selection, dict) or set(selection) - {'node_ids', 'edge_ids'}:
        raise AiInterpretationError('selection solo admite node_ids y edge_ids.')
    node_ids = selection.get('node_ids', [])
    edge_ids = selection.get('edge_ids', [])
    if not isinstance(node_ids, list) or not isinstance(edge_ids, list):
        raise AiInterpretationError('Los elementos de selection deben ser listas.')
    available_nodes = {node.get('id') for node in nodes if isinstance(node, dict)}
    available_edges = {edge.get('id') for edge in edges if isinstance(edge, dict)}
    for field, values, available in (
        ('node_ids', node_ids, available_nodes), ('edge_ids', edge_ids, available_edges),
    ):
        if any(not isinstance(value, str) or value not in available for value in values):
            raise AiInterpretationError(f'{field} contiene un ID inexistente.')
        if len(values) != len(set(values)):
            raise AiInterpretationError(f'{field} no puede repetir IDs.')
    return instruction.strip(), {'node_ids': node_ids, 'edge_ids': edge_ids}


def validate_provider_response(payload, nodes, edges):
    """Ensure the provider response is just one of the public plan variants."""
    if not isinstance(payload, dict):
        raise AiInterpretationError('El proveedor no devolvio un objeto JSON.')
    required = {'status', 'summary', 'question', 'candidates', 'operations'}
    if set(payload) != required:
        raise AiInterpretationError('El proveedor devolvio campos no permitidos.')
    status = payload.get('status')
    if status not in {'ready', 'clarification', 'unsupported'}:
        raise AiInterpretationError('El proveedor devolvio un estado no permitido.')
    for field in ('summary', 'question'):
        if not isinstance(payload[field], str) or len(payload[field]) > 500:
            raise AiInterpretationError(f'El proveedor devolvio {field} invalido.')
    if not isinstance(payload['candidates'], list) or not all(isinstance(item, str) for item in payload['candidates']):
        raise AiInterpretationError('El proveedor devolvio candidates invalido.')
    known_ids = {
        *(node.get('id') for node in nodes if isinstance(node, dict)),
        *(edge.get('id') for edge in edges if isinstance(edge, dict)),
    }
    if any(item not in known_ids for item in payload['candidates']):
        raise AiInterpretationError('El proveedor devolvio candidatos inexistentes.')
    operations = payload['operations']
    if not isinstance(operations, list) or len(operations) > settings.AI_MAX_OPERATIONS:
        raise AiInterpretationError('El proveedor excedio el limite de operaciones.')
    if status == 'ready' and not operations:
        raise AiInterpretationError('Un plan listo requiere operaciones.')
    if status != 'ready' and operations:
        raise AiInterpretationError('Solo un plan listo puede contener operaciones.')
    return {
        'status': status,
        'summary': payload['summary'].strip(),
        'question': payload['question'].strip(),
        'candidates': payload['candidates'],
        'operations': operations,
    }


def _prompt(context):
    """The model may only propose data in the response schema, never tools."""
    return (
        'Eres un interprete de instrucciones para un editor UML. '
        'Devuelve exclusivamente JSON conforme al esquema. No ejecutes codigo, comandos, SQL, URLs '
        'ni acciones fuera de estas operaciones: create_node, update_node, move_node, delete_node, '
        'add_attribute, update_attribute, remove_attribute, create_relation, update_relation, '
        'delete_relation. Usa IDs del contexto; para elementos nuevos usa temp_id. '
        'Si hay ambiguedad, devuelve status clarification, una pregunta y candidatos que sean IDs del contexto. '
        'No inventes permisos ni modifiques el proyecto.\n\n'
        f'Contexto seguro: {json.dumps(context, ensure_ascii=False, separators=(",", ":"))}'
    )
