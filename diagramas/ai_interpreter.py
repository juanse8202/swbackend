"""Provider boundary for phase 2 of the DiagramCraft modelling agent.

The provider receives an intentionally small, read-only summary and can only
propose JSON operations. It never receives a Django model or persistence
capability, and the view validates every proposal with ``execute_operations``.
"""

import json
import os
import re
from uuid import uuid4

from django.conf import settings


class AiProviderError(RuntimeError):
    """A safe, user-facing category for an unavailable AI provider."""


class AiInterpretationError(ValueError):
    """An invalid, unsafe, or ambiguous request/response payload."""


PROJECT_CREATE_PATTERN = re.compile(
    r'^\s*(?:diana\s*,?\s*)?(?:crea|crear|quiero\s+crear)\s+(?:un\s+)?proyecto'
    r'(?:\s+(?:llamado|denominado))?(?:\s+(?P<name>.+?))?\s*[.!?]*\s*$',
    re.IGNORECASE,
)
CLASS_CREATE_PATTERN = re.compile(
    r'^\s*(?:diana\s*,?\s*)?(?:crea|crear)\s+(?:una?\s+)?'
    r'(?P<kind>clase|entidad|interfaz)\s+(?:llamada?\s+)?(?P<body>.+?)\s*[.!?]*\s*$',
    re.IGNORECASE,
)


def project_create_proposal(instruction):
    """Recognize the small, explicit project-creation grammar before Gemini.

    This guarantees that project names come from the user's words and that an
    incomplete request asks a predictable clarification instead of inventing a
    project name.  All other modelling instructions continue to use Gemini.
    """
    match = PROJECT_CREATE_PATTERN.match(instruction or '')
    if not match:
        return None
    name = (match.group('name') or '').strip(' \t\r\n.,;:')
    if not name:
        return {
            'status': 'clarification',
            'summary': '',
            'question': '¿Qué nombre deseas para el proyecto?',
            'candidates': [],
            'operations': [],
        }
    return {
        'status': 'ready',
        'summary': f'Crear proyecto {name}.',
        'question': '',
        'candidates': [],
        'operations': [{
            'id': str(uuid4()),
            'op': 'project.create',
            'payload': {'name': name, 'create_main_diagram': True},
        }],
    }


def class_create_proposal(instruction):
    """Build a typed plan for the explicit Spanish class creation grammar.

    This small parser intentionally runs before the remote provider, so the
    common voice commands work even when Gemini is unavailable.  It never
    writes a document: the returned operation still goes through the normal
    plan, confirmation and atomic apply boundary.
    """
    match = CLASS_CREATE_PATTERN.match(instruction or '')
    if not match:
        return None

    label = match.group('kind').lower()
    kind = {'clase': 'class', 'entidad': 'entity', 'interfaz': 'interface'}[label]
    parts = re.split(r'\s+con\s+', match.group('body').strip(), maxsplit=1, flags=re.IGNORECASE)
    name = ''.join(parts[0].strip(' \t\r\n.,;:').split())
    attributes_part = parts[1].strip(' \t\r\n.,;:') if len(parts) == 2 else ''
    if not name:
        return {
            'status': 'clarification', 'summary': '',
            'question': 'Indica el nombre de la clase, entidad o interfaz.',
            'candidates': [], 'operations': [],
        }
    if not re.fullmatch(r'[^\W\d]\w*', name, flags=re.UNICODE):
        return {
            'status': 'clarification', 'summary': '',
            'question': 'El nombre debe comenzar con una letra y no contener simbolos.',
            'candidates': [], 'operations': [],
        }

    properties = []
    if attributes_part:
        attribute_text = re.sub(r'^(?:los\s+)?atributos?\s+', '', attributes_part, flags=re.IGNORECASE)
        chunks = [part.strip(' \t\r\n.,;:') for part in re.split(r'\s*(?:,|;|\by\b)\s*', attribute_text, flags=re.IGNORECASE)]
        for chunk in filter(None, chunks):
            attribute = re.fullmatch(
                r'(?:el\s+)?(?:atributo\s+)?(?P<name>[^\s,;]+)\s+'
                r'(?:de\s+tipo\s+)?(?P<type>[A-Za-z_][\w<>\[\],?]*)',
                chunk,
                flags=re.IGNORECASE,
            )
            if not attribute:
                return {
                    'status': 'clarification', 'summary': '',
                    'question': f'No entendi el atributo "{chunk}". Di por ejemplo: nombre de tipo String.',
                    'candidates': [], 'operations': [],
                }
            properties.append({
                'name': attribute.group('name'), 'type': attribute.group('type'),
                'visibility': 'private',
            })
    if kind == 'interface' and properties:
        return {
            'status': 'clarification', 'summary': '',
            'question': 'Una interfaz no puede crearse con atributos. Indica solo su nombre o agrega metodos despues.',
            'candidates': [], 'operations': [],
        }

    property_summary = ''
    if properties:
        rendered = [f"{item['name']} de tipo {item['type']}" for item in properties]
        article = 'el atributo' if len(rendered) == 1 else 'los atributos'
        property_summary = f" con {article} " + ', '.join(rendered)
    return {
        'status': 'ready', 'summary': f'Crear {label} {name}{property_summary}.',
        'question': '', 'candidates': [],
        'operations': [{
            'op': 'create_node', 'kind': kind, 'title': name,
            'properties': properties,
        }],
    }


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
    unknown = set(payload) - {'instruction', 'selection', 'expected_revision', 'request_id'}
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
    expected_revision = payload.get('expected_revision')
    if expected_revision is not None and (
        isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0
    ):
        raise AiInterpretationError('expected_revision debe ser un entero no negativo.')
    request_id = payload.get('request_id')
    if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 128):
        raise AiInterpretationError('request_id debe ser texto no vacio de hasta 128 caracteres.')
    return instruction.strip(), {'node_ids': node_ids, 'edge_ids': edge_ids}, expected_revision, request_id


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
        'delete_relation, project.create. project.create requiere un id UUID y payload con name y create_main_diagram=true. '
        'Usa IDs del contexto; para elementos nuevos usa temp_id. '
        'Si hay ambiguedad, devuelve status clarification, una pregunta y candidatos que sean IDs del contexto. '
        'Si el usuario pide crear proyecto y no da un nombre claro, devuelve clarification. '
        'No inventes permisos ni modifiques el proyecto.\n\n'
        f'Contexto seguro: {json.dumps(context, ensure_ascii=False, separators=(",", ":"))}'
    )
