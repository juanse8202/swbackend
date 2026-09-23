from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from io import BytesIO
from zipfile import ZipFile
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from lxml import etree
from django.utils import timezone

from proyectos.models import Proyecto, ProyectoMiembro
from .models import Diagrama, PlanIA
from .ai_operations import OperationError, execute_operations
from .ai_interpreter import AiProviderError
from .ai_plans import create_plan
from .spring_generator import generate_spring_boot_zip
from .xmi import UMLDI, XMI, XmiError, export_xmi, parse_xmi


class FakeAiProvider:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.contexts = []

    def interpret(self, context):
        self.contexts.append(context)
        if self.error:
            raise self.error
        return self.response


class DiagramUmlContractTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='arquitecta', password='clave-segura'
        )
        self.project = Proyecto.objects.create(nombre='Contrato UML', creador=self.user)
        self.diagram = Diagrama.objects.create(proyecto=self.project, nombre='Principal')
        self.client.force_login(self.user)

    @staticmethod
    def node(node_id, kind):
        properties = ([{'name': 'id', 'type': 'UUID (@Id)'}]
                      if kind == 'entity' else [])
        return {
            'id': node_id,
            'type': 'umlClass',
            'position': {'x': 0, 'y': 0},
            'data': {'kind': kind, 'title': f'{node_id}.java', 'properties': properties, 'methods': []},
        }

    @staticmethod
    def edge(edge_id, source, target, relation_type):
        return {
            'id': edge_id,
            'source': source,
            'target': target,
            'type': 'relationEdge',
            'data': {'relationType': relation_type, 'multiplicidadOrigen': '1', 'multiplicidadDestino': '1'},
        }

    def patch_document(self, nodes, edges):
        return self.client.patch(
            f'/api/diagramas/diagramas/{self.diagram.id}/',
            {'nodes': nodes, 'edges': edges},
            content_type='application/json',
        )

    def test_ai_operations_create_update_move_attributes_and_relations(self):
        nodes = [self.node('cliente', 'entity'), self.node('pedido', 'entity')]
        candidate = execute_operations(nodes, [], [
            {'op': 'update_node', 'node_id': 'cliente', 'patch': {'title': 'Persona'}},
            {'op': 'move_node', 'node_id': 'cliente', 'position': {'x': 250, 'y': 80}},
            {'op': 'add_attribute', 'node_id': 'cliente',
             'attribute': {'name': 'nombre', 'type': 'String', 'visibility': 'private'}},
            {'op': 'update_attribute', 'node_id': 'cliente', 'attribute_name': 'nombre',
             'attribute': {'name': 'nombreCompleto', 'type': 'String'}},
            {'op': 'create_relation', 'source': 'cliente', 'target': 'pedido',
             'relation_type': 'asociacion',
             'data': {'multiplicidadOrigen': '1', 'multiplicidadDestino': '*',
                      'ownerNodeId': 'cliente', 'umlLabel': 'realiza'}},
        ])
        cliente = next(node for node in candidate['nodes'] if node['id'] == 'cliente')
        self.assertEqual(cliente['data']['title'], 'Persona.java')
        self.assertEqual(cliente['position'], {'x': 250, 'y': 80})
        self.assertIn('nombreCompleto', [item['name'] for item in cliente['data']['properties']])
        self.assertEqual(candidate['edges'][0]['data']['umlLabel'], 'realiza')

        candidate = execute_operations(candidate['nodes'], candidate['edges'], [
            {'op': 'update_relation', 'edge_id': candidate['edges'][0]['id'],
             'data': {'multiplicidadDestino': '0..*'}},
            {'op': 'delete_relation', 'edge_id': candidate['edges'][0]['id']},
            {'op': 'remove_attribute', 'node_id': 'cliente', 'attribute_name': 'nombreCompleto'},
        ])
        self.assertEqual(candidate['edges'], [])
        cliente = next(node for node in candidate['nodes'] if node['id'] == 'cliente')
        self.assertNotIn('nombreCompleto', [item['name'] for item in cliente['data']['properties']])

    def test_ai_operations_support_temp_references_and_cascade_delete(self):
        candidate = execute_operations([], [], [
            {'op': 'create_node', 'temp_id': '$pedido', 'kind': 'entity', 'title': 'Pedido'},
            {'op': 'create_node', 'temp_id': '$detalle', 'kind': 'entity', 'title': 'DetallePedido'},
            {'op': 'create_relation', 'source': '$detalle', 'target': '$pedido',
             'relation_type': 'composicion',
             'data': {'multiplicidadOrigen': '1..*', 'multiplicidadDestino': '1',
                      'wholeNodeId': '$pedido', 'ownerNodeId': '$pedido',
                      'relationConvention': 'target-whole-v1'}},
        ])
        # References in relation metadata are explicit IDs, never unresolved temporary aliases.
        relation = candidate['edges'][0]
        self.assertEqual(relation['data']['wholeNodeId'], relation['target'])
        with self.assertRaises(OperationError):
            execute_operations(candidate['nodes'], candidate['edges'], [
                {'op': 'delete_node', 'node_id': relation['target']},
            ])
        candidate = execute_operations(candidate['nodes'], candidate['edges'], [
            {'op': 'delete_node', 'node_id': relation['target'], 'cascade_incident': True},
        ])
        self.assertEqual(len(candidate['nodes']), 1)
        self.assertEqual(candidate['edges'], [])

    def test_ai_operations_reject_invalid_batches_without_mutating_inputs(self):
        nodes = [self.node('cliente', 'entity')]
        original_nodes = deepcopy(nodes)
        with self.assertRaises(OperationError):
            execute_operations(nodes, [], [
                {'op': 'add_attribute', 'node_id': 'cliente',
                 'attribute': {'name': 'correo', 'type': 'String'}},
                {'op': 'create_relation', 'source': 'cliente', 'target': 'missing',
                 'relation_type': 'asociacion'},
            ])
        self.assertEqual(nodes, original_nodes)
        with self.assertRaises(OperationError):
            execute_operations(nodes, [], [
                {'op': 'move_node', 'node_id': 'cliente',
                 'position': {'x': 0, 'y': 0}, 'unsafe': 'ignored'},
            ])

    def test_ai_interpretation_returns_valid_plan_without_persisting(self):
        self.diagram.nodes = [self.node('cliente', 'entity')]
        self.diagram.save()
        provider = FakeAiProvider({
            'status': 'ready', 'summary': 'Renombrar Cliente.', 'question': '', 'candidates': [],
            'operations': [{'op': 'update_node', 'node_id': 'cliente',
                            'patch': {'title': 'Persona'}}],
        })
        with patch('diagramas.views.get_ai_provider', return_value=provider):
            response = self.client.post(
                f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
                {'instruction': 'Renombra Cliente a Persona',
                 'selection': {'node_ids': ['cliente'], 'edge_ids': []}},
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ready')
        self.assertIn('plan_id', response.json())
        self.assertEqual(provider.contexts[0]['selection']['node_ids'], ['cliente'])
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.nodes[0]['data']['title'], 'cliente.java')
        self.assertEqual(PlanIA.objects.filter(diagrama=self.diagram).count(), 1)

    def test_ai_interpretation_returns_clarification_without_mutation(self):
        self.diagram.nodes = [self.node('cliente-a', 'entity'), self.node('cliente-b', 'entity')]
        self.diagram.save()
        provider = FakeAiProvider({
            'status': 'clarification', 'summary': '', 'question': 'Cual Cliente?',
            'candidates': ['cliente-a', 'cliente-b'], 'operations': [],
        })
        with patch('diagramas.views.get_ai_provider', return_value=provider):
            response = self.client.post(
                f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
                {'instruction': 'Renombra Cliente'}, content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'clarification')
        self.diagram.refresh_from_db()
        self.assertEqual(len(self.diagram.nodes), 2)

    def test_ai_interpretation_rejects_invalid_provider_payload_and_errors(self):
        self.diagram.nodes = [self.node('cliente', 'entity')]
        self.diagram.save()
        invalid = FakeAiProvider({
            'status': 'ready', 'summary': 'x', 'question': '', 'candidates': [],
            'operations': [{'op': 'eval', 'source': 'cliente'}],
        })
        with patch('diagramas.views.get_ai_provider', return_value=invalid):
            invalid_response = self.client.post(
                f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
                {'instruction': 'haz algo'}, content_type='application/json',
            )
        self.assertEqual(invalid_response.status_code, 400)
        failing = FakeAiProvider(error=AiProviderError('network'))
        with patch('diagramas.views.get_ai_provider', return_value=failing):
            failure_response = self.client.post(
                f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
                {'instruction': 'haz algo'}, content_type='application/json',
            )
        self.assertEqual(failure_response.status_code, 400)
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.nodes[0]['id'], 'cliente')

    def test_ai_interpretation_requires_edit_permission_and_strict_request(self):
        reader = get_user_model().objects.create_user(username='lector-ia', password='clave-segura')
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=reader, rol=ProyectoMiembro.Rol.LECTOR
        )
        self.client.force_login(reader)
        response = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
            {'instruction': 'crear clase'}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.user)
        invalid_request = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
            {'instruction': 'crear clase', 'unsafe': 'x'}, content_type='application/json',
        )
        self.assertEqual(invalid_request.status_code, 400)

    def test_ai_plan_applies_once_atomically_and_is_idempotent(self):
        self.diagram.nodes = [self.node('cliente', 'entity')]
        self.diagram.save()
        plan = create_plan(
            user=self.user, diagram=self.diagram,
            operations=[{'op': 'update_node', 'node_id': 'cliente',
                         'patch': {'title': 'Persona'}}],
        )
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.nodes[0]['data']['title'], 'cliente.java')
        payload = {'plan_id': str(plan.plan_id), 'idempotency_key': 'rename-cliente-1', 'confirm': False}
        response = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/', payload,
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revision'], 1)
        retry = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/', payload,
            content_type='application/json',
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json(), response.json())
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.nodes[0]['data']['title'], 'Persona.java')
        self.assertEqual(self.diagram.revision, 1)

    def test_ai_plan_rejects_confirmation_conflict_expiry_and_invalid_batch(self):
        self.diagram.nodes = [self.node('cliente', 'entity')]
        self.diagram.save()
        delete_plan = create_plan(
            user=self.user, diagram=self.diagram,
            operations=[{'op': 'delete_node', 'node_id': 'cliente', 'cascade_incident': True}],
        )
        denied_delete = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/',
            {'plan_id': str(delete_plan.plan_id), 'idempotency_key': 'delete-1', 'confirm': False},
            content_type='application/json',
        )
        self.assertEqual(denied_delete.status_code, 400)
        self.diagram.refresh_from_db()
        self.assertEqual(len(self.diagram.nodes), 1)

        conflict_plan = create_plan(
            user=self.user, diagram=self.diagram,
            operations=[{'op': 'move_node', 'node_id': 'cliente', 'position': {'x': 20, 'y': 30}}],
        )
        self.diagram.revision += 1
        self.diagram.save(update_fields=['revision'])
        conflict = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/',
            {'plan_id': str(conflict_plan.plan_id), 'idempotency_key': 'conflict-1', 'confirm': False},
            content_type='application/json',
        )
        self.assertEqual(conflict.status_code, 400)

        expired_plan = create_plan(
            user=self.user, diagram=self.diagram,
            operations=[{'op': 'move_node', 'node_id': 'cliente', 'position': {'x': 40, 'y': 50}}],
        )
        expired_plan.expira_en = timezone.now() - timedelta(seconds=1)
        expired_plan.save(update_fields=['expira_en'])
        expired = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/',
            {'plan_id': str(expired_plan.plan_id), 'idempotency_key': 'expired-1', 'confirm': False},
            content_type='application/json',
        )
        self.assertEqual(expired.status_code, 400)

        self.diagram.refresh_from_db()
        invalid_plan = PlanIA.objects.create(
            diagrama=self.diagram, usuario=self.user,
            operaciones=[{'op': 'move_node', 'node_id': 'missing', 'position': {'x': 1, 'y': 2}}],
            revision_base=self.diagram.revision, request_hash='a' * 64,
            expira_en=timezone.now() + timedelta(minutes=1),
        )
        invalid = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/',
            {'plan_id': str(invalid_plan.plan_id), 'idempotency_key': 'invalid-1', 'confirm': False},
            content_type='application/json',
        )
        self.assertEqual(invalid.status_code, 400)
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.nodes[0]['position'], {'x': 0, 'y': 0})

    def test_ai_plan_rejects_other_user_and_editor_relation_changes(self):
        self.diagram.nodes = [self.node('cliente', 'entity'), self.node('pedido', 'entity')]
        self.diagram.save()
        editor = get_user_model().objects.create_user(username='editor-ia', password='clave-segura')
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=editor, rol=ProyectoMiembro.Rol.EDITOR
        )
        owner_plan = create_plan(
            user=self.user, diagram=self.diagram,
            operations=[{'op': 'move_node', 'node_id': 'cliente', 'position': {'x': 1, 'y': 2}}],
        )
        self.client.force_login(editor)
        other_user = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/',
            {'plan_id': str(owner_plan.plan_id), 'idempotency_key': 'other-user', 'confirm': False},
            content_type='application/json',
        )
        self.assertEqual(other_user.status_code, 400)

        editor_plan = create_plan(
            user=editor, diagram=self.diagram,
            operations=[{
                'op': 'create_relation', 'source': 'cliente', 'target': 'pedido',
                'relation_type': 'asociacion',
                'data': {'multiplicidadOrigen': '1', 'multiplicidadDestino': '*'},
            }],
        )
        relation_denied = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/',
            {'plan_id': str(editor_plan.plan_id), 'idempotency_key': 'editor-edge', 'confirm': False},
            content_type='application/json',
        )
        self.assertEqual(relation_denied.status_code, 403)

    def test_realization_requires_an_interface_target(self):
        nodes = [self.node('pedido', 'entity'), self.node('notificable', 'interface')]
        valid = self.patch_document(
            nodes, [self.edge('pedido-notificable', 'pedido', 'notificable', 'realizacion')]
        )
        self.assertEqual(valid.status_code, 200)

        invalid = self.patch_document(
            [self.node('pedido', 'entity'), self.node('cliente', 'entity')],
            [self.edge('pedido-cliente', 'pedido', 'cliente', 'realizacion')],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('edges', invalid.json())

    def test_inheritance_rejects_interface_target_self_cycle_and_multiple_parents(self):
        interface_target = self.patch_document(
            [self.node('hija', 'entity'), self.node('contrato', 'interface')],
            [self.edge('hija-contrato', 'hija', 'contrato', 'herencia')],
        )
        self.assertEqual(interface_target.status_code, 400)

        self_inheritance = self.patch_document(
            [self.node('hija', 'entity')],
            [self.edge('hija-hija', 'hija', 'hija', 'herencia')],
        )
        self.assertEqual(self_inheritance.status_code, 400)

        cycle = self.patch_document(
            [self.node('a', 'entity'), self.node('b', 'entity')],
            [
                self.edge('a-b', 'a', 'b', 'herencia'),
                self.edge('b-a', 'b', 'a', 'herencia'),
            ],
        )
        self.assertEqual(cycle.status_code, 400)

        multiple_parents = self.patch_document(
            [self.node('hija', 'entity'), self.node('padre-a', 'entity'), self.node('padre-b', 'entity')],
            [
                self.edge('hija-padre-a', 'hija', 'padre-a', 'herencia'),
                self.edge('hija-padre-b', 'hija', 'padre-b', 'herencia'),
            ],
        )
        self.assertEqual(multiple_parents.status_code, 400)

    def test_invalid_relation_endpoint_and_multiplicity_are_rejected(self):
        response = self.patch_document(
            [self.node('pedido', 'entity')],
            [{
                **self.edge('pedido-inexistente', 'pedido', 'inexistente', 'asociacion'),
                'data': {'relationType': 'asociacion', 'multiplicidadOrigen': 'varios', 'multiplicidadDestino': '1'},
            }],
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('edges', response.json())

    def test_owner_generates_maven_zip_with_extends_and_implements(self):
        nodes = [
            self.node('persona', 'entity'),
            self.node('cliente', 'entity'),
            self.node('notificable', 'interface'),
        ]
        nodes[0]['data']['title'] = 'Persona.java'
        nodes[0]['data']['properties'] = [{'name': 'id', 'type': 'UUID (@Id)'}]
        nodes[1]['data']['title'] = 'Cliente.java'
        nodes[1]['data']['properties'] = [{'name': 'id', 'type': 'UUID (@Id)'}]
        nodes[2]['data']['title'] = 'Notificable.java'
        nodes[2]['data']['methods'] = ['notificar(): void']
        self.diagram.nodes = nodes
        self.diagram.edges = [
            self.edge('cliente-persona', 'cliente', 'persona', 'herencia'),
            self.edge('cliente-notificable', 'cliente', 'notificable', 'realizacion'),
        ]
        self.diagram.save()

        response = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/generar-spring-boot/',
            {'artifact': 'ventas', 'package': 'com.example.ventas'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        with ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
            self.assertIn('ventas/pom.xml', names)
            self.assertIn('ventas/src/main/java/com/example/ventas/model/Notificable.java', names)
            self.assertIn('ventas/src/main/resources/application.properties', names)
            persona = archive.read('ventas/src/main/java/com/example/ventas/entity/Persona.java').decode()
            client = archive.read('ventas/src/main/java/com/example/ventas/entity/Cliente.java').decode()
            controller = archive.read('ventas/src/main/java/com/example/ventas/controller/PersonaController.java').decode()
            service = archive.read('ventas/src/main/java/com/example/ventas/service/PersonaService.java').decode()
            mapper = archive.read('ventas/src/main/java/com/example/ventas/mapper/PersonaMapper.java').decode()
            properties = archive.read('ventas/src/main/resources/application.properties').decode()
        self.assertIn('@Entity', persona)
        self.assertIn('class Cliente extends Persona implements Notificable', client)
        self.assertIn('@GetMapping', controller)
        self.assertIn('@PostMapping', controller)
        self.assertIn('@PutMapping', controller)
        self.assertIn('@DeleteMapping', controller)
        self.assertIn('ResponseEntity', controller)
        self.assertIn('findAll()', service)
        self.assertIn('toDto', mapper)
        self.assertIn('${DB_PASSWORD:}', properties)

    def test_generator_uses_pascal_case_crud_and_skips_interface_layers(self):
        entity = self.node('auto', 'entity')
        entity['data']['title'] = 'auto.java'
        entity['data']['properties'] = [
            {'name': 'id', 'type': 'UUID (@Id)'},
            {'name': 'placa', 'type': 'String'},
        ]
        interface = self.node('conducible', 'interface')
        interface['data']['title'] = 'conducible.java'
        interface['data']['methods'] = ['conducir(): void']

        payload, _ = generate_spring_boot_zip(
            [entity, interface], [], artifact='crud', package='com.example.crud'
        )

        with ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            controller = archive.read(
                'crud/src/main/java/com/example/crud/controller/AutoController.java'
            ).decode()
            service = archive.read(
                'crud/src/main/java/com/example/crud/service/AutoService.java'
            ).decode()
        self.assertIn('public class AutoController', controller)
        self.assertIn('@GetMapping', controller)
        self.assertIn('public class AutoService', service)
        self.assertNotIn(
            'crud/src/main/java/com/example/crud/controller/ConducibleController.java', names
        )
        self.assertNotIn(
            'crud/src/main/java/com/example/crud/repository/ConducibleRepository.java', names
        )

    def test_generator_outputs_all_node_kinds_and_embedded_fields(self):
        pedido = self.node('pedido', 'entity')
        pedido['data']['title'] = 'Pedido.java'
        pedido['data']['properties'] = [
            {'name': 'id', 'type': 'UUID (@Id)'},
            {'name': 'estado', 'type': 'Estado'},
            {'name': 'direccion', 'type': 'Direccion', 'embedded': True},
        ]
        base = self.node('auditable', 'class')
        base['data'].update({'title': 'Auditable.java', 'abstract': True})
        interface = self.node('notificable', 'interface')
        interface['data'].update({'title': 'Notificable.java', 'methods': ['notificar(): void']})
        explicit_dto = self.node('pedido-resumen', 'dto')
        explicit_dto['data'].update({
            'title': 'PedidoResumen.java',
            'properties': [{'name': 'id', 'type': 'UUID'}],
        })
        enum = self.node('estado', 'enum')
        enum['data'].update({'title': 'Estado.java', 'literals': ['pendiente', 'pagado']})
        embeddable = self.node('direccion', 'embeddable')
        embeddable['data'].update({
            'title': 'Direccion.java',
            'properties': [{'name': 'calle', 'type': 'String'}],
        })

        payload, _ = generate_spring_boot_zip(
            [pedido, base, interface, explicit_dto, enum, embeddable],
            [
                self.edge('pedido-base', 'pedido', 'auditable', 'herencia'),
                self.edge('pedido-interface', 'pedido', 'notificable', 'realizacion'),
            ],
            artifact='tipos', package='com.example.tipos',
        )

        with ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            entity = archive.read('tipos/src/main/java/com/example/tipos/entity/Pedido.java').decode()
            model_class = archive.read('tipos/src/main/java/com/example/tipos/model/Auditable.java').decode()
            interface_source = archive.read('tipos/src/main/java/com/example/tipos/model/Notificable.java').decode()
            enum_source = archive.read('tipos/src/main/java/com/example/tipos/model/Estado.java').decode()
            embeddable_source = archive.read('tipos/src/main/java/com/example/tipos/model/Direccion.java').decode()
            dto_source = archive.read('tipos/src/main/java/com/example/tipos/dto/PedidoResumen.java').decode()
        self.assertIn('class Pedido extends Auditable implements Notificable', entity)
        self.assertIn('@Embedded', entity)
        self.assertIn('import com.example.tipos.model.Direccion;', entity)
        self.assertIn('public abstract class Auditable', model_class)
        self.assertIn('public interface Notificable', interface_source)
        self.assertIn('public enum Estado', enum_source)
        self.assertIn('PENDIENTE', enum_source)
        self.assertIn('@Embeddable', embeddable_source)
        self.assertIn('public record PedidoResumen', dto_source)
        self.assertNotIn('tipos/src/main/java/com/example/tipos/controller/EstadoController.java', names)
        self.assertNotIn('tipos/src/main/java/com/example/tipos/controller/DireccionController.java', names)

    def test_generator_maps_jpa_multiplicity_ownership_and_composition(self):
        pedido = self.node('pedido', 'entity')
        pedido['data']['title'] = 'Pedido.java'
        linea = self.node('linea', 'entity')
        linea['data']['title'] = 'Linea.java'
        etiqueta = self.node('etiqueta', 'entity')
        etiqueta['data']['title'] = 'Etiqueta.java'
        edges = [
            {
                **self.edge('pedido-lineas', 'pedido', 'linea', 'composicion'),
                'data': {
                    'relationType': 'composicion', 'multiplicidadOrigen': '1',
                    'multiplicidadDestino': 'N', 'ownerNodeId': 'pedido',
                    'wholeNodeId': 'pedido', 'sourceRole': 'lineas',
                    'targetRole': 'pedido', 'bidirectional': False,
                },
            },
            {
                **self.edge('pedido-etiquetas', 'pedido', 'etiqueta', 'asociacion'),
                'data': {
                    'relationType': 'asociacion', 'multiplicidadOrigen': 'N',
                    'multiplicidadDestino': 'N', 'ownerNodeId': 'pedido',
                    'sourceRole': 'etiquetas', 'targetRole': 'pedidos',
                    'bidirectional': True,
                },
            },
        ]
        payload, _ = generate_spring_boot_zip(
            [pedido, linea, etiqueta], edges, artifact='relaciones', package='com.example.relaciones'
        )
        with ZipFile(BytesIO(payload)) as archive:
            pedido_source = archive.read('relaciones/src/main/java/com/example/relaciones/entity/Pedido.java').decode()
            etiqueta_source = archive.read('relaciones/src/main/java/com/example/relaciones/entity/Etiqueta.java').decode()
        self.assertIn('@OneToMany(cascade = CascadeType.ALL, orphanRemoval = true)', pedido_source)
        self.assertIn('List<Linea> lineas', pedido_source)
        self.assertIn('@ManyToMany', pedido_source)
        self.assertIn('@JoinTable(name = "pedido_etiqueta")', pedido_source)
        self.assertIn('@ManyToMany(mappedBy = "etiquetas")', etiqueta_source)

    def test_xmi_round_trip_preserves_entity_interface_and_realization(self):
        persona = self.node('persona', 'entity')
        persona['data'].update({'title': 'Persona.java', 'properties': [{'name': 'id', 'type': 'UUID (@Id)'}]})
        contract = self.node('notificable', 'interface')
        contract['data'].update({'title': 'Notificable.java', 'methods': ['notificar(): void']})
        xml = export_xmi([persona, contract], [self.edge('realiza', 'persona', 'notificable', 'realizacion')])
        imported = parse_xmi(xml)
        self.assertEqual(imported['report']['imported'], {'nodes': 2, 'edges': 1})
        self.assertEqual({node['data']['kind'] for node in imported['nodes']}, {'entity', 'interface'})
        self.assertEqual(imported['edges'][0]['data']['relationType'], 'realizacion')

    def test_xmi_rejects_dtd_before_parsing(self):
        with self.assertRaises(XmiError) as error:
            parse_xmi(b'<!DOCTYPE x [<!ENTITY bad "x">]><xmi:XMI/>')
        self.assertEqual(error.exception.error['code'], 'unsafe_xml')

    def test_xmi_21_transport_with_uml_namespace_is_accepted(self):
        ea_transport = b'''<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/spec/XMI/20131001" xmlns:uml="http://www.omg.org/spec/UML/20110701" xmi:version="2.1" exporter="Enterprise Architect"><uml:Model xmi:id="m"><packagedElement xmi:type="uml:Class" xmi:id="cliente" name="Cliente"/></uml:Model></xmi:XMI>'''
        imported = parse_xmi(ea_transport)
        self.assertEqual(imported['report']['imported']['nodes'], 1)

    def test_ea_xmi_without_version_imports_plain_class_association(self):
        # EA 6.x exports an official UML 2.x namespace but omits xmi:version.
        # Its associations between regular classes are UML metadata, not JPA.
        ea_transport = b'''<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/spec/XMI/20131001" xmlns:uml="http://www.omg.org/spec/UML/20131001"><xmi:Documentation exporter="Enterprise Architect" exporterVersion="6.5"/><uml:Model xmi:id="m"><packagedElement xmi:type="uml:Class" xmi:id="cliente" name="Cliente"/><packagedElement xmi:type="uml:Class" xmi:id="pedido" name="Pedido"/><packagedElement xmi:type="uml:Association" xmi:id="cliente-pedido" name="realiza"><ownedEnd xmi:id="end-cliente" type="cliente"/><ownedEnd xmi:id="end-pedido" type="pedido"/></packagedElement></uml:Model></xmi:XMI>'''
        uploaded = SimpleUploadedFile('ea-6.x.xmi', ea_transport, content_type='application/xml')
        response = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/importar-xmi/',
            {'file': uploaded, 'mode': 'replace'},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.diagram.refresh_from_db()
        self.assertEqual(len(self.diagram.nodes), 2)
        self.assertEqual(len(self.diagram.edges), 1)
        self.assertFalse(self.diagram.edges[0]['data']['jpaManaged'])
        self.assertEqual(self.diagram.edges[0]['data']['umlLabel'], 'realiza')
        self.assertEqual(self.diagram.edges[0]['data']['multiplicidadOrigen'], '')
        self.assertEqual(self.diagram.edges[0]['data']['multiplicidadDestino'], '')

    def test_xmi_rejects_duplicate_ids_and_is_deterministic(self):
        duplicate = b'''<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/spec/XMI/20131001" xmlns:uml="http://www.omg.org/spec/UML/20131001" xmi:version="2.5"><uml:Model xmi:id="m"><packagedElement xmi:type="uml:Class" xmi:id="same" name="A"/><packagedElement xmi:type="uml:Class" xmi:id="same" name="B"/></uml:Model></xmi:XMI>'''
        with self.assertRaises(XmiError) as error:
            parse_xmi(duplicate)
        self.assertEqual(error.exception.error['code'], 'duplicate_xmi_id')

        nodes = [self.node('a', 'entity'), self.node('b', 'entity')]
        edges = [self.edge('a-b', 'a', 'b', 'asociacion')]
        self.assertEqual(export_xmi(nodes, edges), export_xmi(nodes, edges))

    def test_xmi_keeps_distinct_named_associations_with_same_endpoints(self):
        source = b'''<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/spec/XMI/20131001" xmlns:uml="http://www.omg.org/spec/UML/20131001" xmi:version="2.5"><uml:Model xmi:id="m"><packagedElement xmi:type="uml:Class" xmi:id="user" name="User"/><packagedElement xmi:type="uml:Class" xmi:id="record" name="Record"/><packagedElement xmi:type="uml:Association" xmi:id="created" name="created_by"><ownedEnd type="user" lower="0" upper="1"/><ownedEnd type="record" lower="0" upper="*"/></packagedElement><packagedElement xmi:type="uml:Association" xmi:id="deleted" name="deleted_by"><ownedEnd type="user" lower="0" upper="1"/><ownedEnd type="record" lower="0" upper="*"/></packagedElement></uml:Model></xmi:XMI>'''
        imported = parse_xmi(source)
        self.assertEqual(imported['report']['imported']['edges'], 2)
        self.assertEqual({edge['data']['umlLabel'] for edge in imported['edges']}, {'created_by', 'deleted_by'})

    def test_xmi_import_permissions_and_atomic_replace(self):
        self.diagram.nodes = [self.node('original', 'entity')]
        self.diagram.edges = []
        self.diagram.save()
        invalid = SimpleUploadedFile('invalid.xmi', b'<xmi:XMI>', content_type='application/xml')
        response = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/importar-xmi/', {'file': invalid, 'mode': 'replace'}
        )
        self.assertEqual(response.status_code, 400)
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.nodes[0]['id'], 'original')

        editor = get_user_model().objects.create_user(username='xmi-editor', password='clave-segura')
        ProyectoMiembro.objects.create(proyecto=self.project, usuario=editor, rol=ProyectoMiembro.Rol.EDITOR)
        self.client.force_login(editor)
        denied = self.client.get(f'/api/diagramas/diagramas/{self.diagram.id}/exportar-xmi/')
        self.assertEqual(denied.status_code, 403)

    def test_xmi_round_trip_maps_all_six_relation_types(self):
        source = self.node('source', 'entity')
        target = self.node('target', 'entity')
        contract = self.node('contract', 'interface')
        source['position'] = {'x': 120, 'y': 240}
        target['position'] = {'x': 620, 'y': 240}
        source['data']['xmiId'] = 'EAID_existing_source'
        target['data']['xmiId'] = 'EAID_existing_target'
        relations = [
            {
                **self.edge('association', 'source', 'target', 'asociacion'),
                'data': {
                    'relationType': 'asociacion', 'multiplicidadOrigen': '',
                    'multiplicidadDestino': '', 'umlLabel': 'crea',
                },
            },
            {**self.edge('aggregation', 'source', 'target', 'agregacion'), 'data': {'relationType': 'agregacion', 'multiplicidadOrigen': '1', 'multiplicidadDestino': 'N', 'wholeNodeId': 'source'}},
            {**self.edge('composition', 'target', 'source', 'composicion'), 'data': {'relationType': 'composicion', 'multiplicidadOrigen': '1', 'multiplicidadDestino': 'N', 'wholeNodeId': 'target'}},
            self.edge('inheritance', 'target', 'source', 'herencia'),
            self.edge('realization', 'source', 'contract', 'realizacion'),
            self.edge('dependency', 'source', 'target', 'dependencia'),
        ]
        exported = export_xmi([source, target, contract], relations)
        self.assertEqual(exported, export_xmi([source, target, contract], relations))

        root = etree.fromstring(exported)
        diagram = root.find(f'{{{UMLDI}}}Diagram')
        self.assertIsNotNone(diagram)
        xmi_type = f'{{{XMI}}}type'
        package = next(
            item for item in root.iter()
            if item.get(xmi_type) == 'uml:Package'
            and item.get(f'{{{XMI}}}id') == 'dc-package-diagramcraft'
        )
        self.assertEqual(package.get('name'), 'DiagramCraft')
        self.assertEqual(diagram.get('modelElement'), package.get(f'{{{XMI}}}id'))
        extension = root.find(f'{{{XMI}}}Extension')
        self.assertIsNotNone(extension)
        ea_diagram = extension.find('diagrams/diagram')
        self.assertIsNotNone(ea_diagram)
        self.assertEqual(ea_diagram.get(f'{{{XMI}}}id'), diagram.get(f'{{{XMI}}}id'))
        self.assertEqual(ea_diagram.find('model').get('owner'), package.get(f'{{{XMI}}}id'))
        self.assertEqual(ea_diagram.find('properties').get('name'), 'DiagramCraft')
        self.assertEqual(len(ea_diagram.find('elements')), 3 + 6)
        xmi_ids = [
            item.get(f'{{{XMI}}}id') for item in root.iter()
            if item.get(f'{{{XMI}}}id')
        ]
        self.assertEqual(
            {value for value in xmi_ids if xmi_ids.count(value) > 1},
            {'dc-diagram-0'},
        )
        shapes = [
            item for item in diagram
            if item.get(xmi_type) == 'umldi:UMLClassifierShape'
        ]
        self.assertEqual(len(shapes), 3)
        self.assertEqual(
            {shape.get('modelElement') for shape in shapes},
            {'dc-node-0', 'dc-node-1', 'dc-node-2'},
        )
        self.assertNotIn(b'EAID_existing_source', exported)
        self.assertNotIn(b'EAID_existing_target', exported)
        self.assertTrue(all(shape.find('bounds') is not None for shape in shapes))
        source_shape = next(shape for shape in shapes if shape.get('modelElement') == 'dc-node-0')
        self.assertEqual(source_shape.find('bounds').get('x'), '120')
        self.assertEqual(source_shape.find('bounds').get('y'), '240')

        diagram_edges = [
            item for item in diagram if item.get(xmi_type) == 'umldi:UMLEdge'
        ]
        self.assertEqual(len(diagram_edges), 6)
        self.assertEqual(
            {edge.get('modelElement') for edge in diagram_edges},
            {f'dc-rel-{index}' for index in range(6)},
        )
        self.assertTrue(all(len(edge.findall('waypoint')) == 2 for edge in diagram_edges))

        associations = [
            item for item in root.iter()
            if item.get(xmi_type) == 'uml:Association'
        ]
        self.assertEqual(len(associations), 3)
        association = next(item for item in associations if item.get('name') == 'crea')
        self.assertEqual(len(association.findall('ownedEnd')), 2)
        self.assertEqual(len(association.findall('memberEnd')), 2)
        self.assertTrue(all(end.get('lower') is None and end.get('upper') is None
                            for end in association.findall('ownedEnd')))
        self.assertTrue(all(not end.findall('lowerValue') and not end.findall('upperValue')
                            for end in association.findall('ownedEnd')))

        aggregation = next(item for item in associations if item.get('name') is None)
        aggregation_ends = aggregation.findall('ownedEnd')
        self.assertEqual(
            [end.find('lowerValue').get('value') for end in aggregation_ends], ['1', '0']
        )
        self.assertEqual(
            [end.find('upperValue').get('value') for end in aggregation_ends], ['1', '*']
        )
        self.assertEqual(aggregation_ends[0].get('aggregation'), 'shared')

        association_edge = next(edge for edge in diagram_edges if edge.get('modelElement') == 'dc-rel-0')
        self.assertFalse(any(item.get(xmi_type) == 'umldi:UMLMultiplicityLabel'
                             for item in association_edge))
        aggregation_edge = next(edge for edge in diagram_edges if edge.get('modelElement') == 'dc-rel-1')
        multiplicity_labels = [
            item for item in aggregation_edge
            if item.get(xmi_type) == 'umldi:UMLMultiplicityLabel'
        ]
        self.assertEqual({item.get('text') for item in multiplicity_labels}, {'1', '0..*'})
        self.assertEqual(
            {item.get('modelElement') for item in multiplicity_labels},
            {'dc-end-1-0', 'dc-end-1-1'},
        )

        imported = parse_xmi(exported)
        self.assertEqual(
            {edge['data']['relationType'] for edge in imported['edges']},
            {'asociacion', 'agregacion', 'composicion', 'herencia', 'realizacion', 'dependencia'},
        )
        composition = next(edge for edge in imported['edges'] if edge['data']['relationType'] == 'composicion')
        self.assertEqual(composition['data']['ownerNodeId'], composition['data']['wholeNodeId'])
        imported_aggregation = next(edge for edge in imported['edges'] if edge['data']['relationType'] == 'agregacion')
        self.assertEqual(imported_aggregation['data']['multiplicidadOrigen'], '1')
        self.assertEqual(imported_aggregation['data']['multiplicidadDestino'], '*')

    def test_xmi_export_keeps_parallel_associations_with_distinct_labels(self):
        source = self.node('source', 'entity')
        target = self.node('target', 'entity')
        relations = [
            {
                **self.edge('created-by', 'source', 'target', 'asociacion'),
                'data': {
                    'relationType': 'asociacion', 'multiplicidadOrigen': '',
                    'multiplicidadDestino': '', 'umlLabel': 'created_by',
                },
            },
            {
                **self.edge('updated-by', 'source', 'target', 'asociacion'),
                'data': {
                    'relationType': 'asociacion', 'multiplicidadOrigen': '',
                    'multiplicidadDestino': '', 'umlLabel': 'updated_by',
                },
            },
        ]
        root = etree.fromstring(export_xmi([source, target], relations))
        xmi_type = f'{{{XMI}}}type'
        associations = [
            item for item in root.iter()
            if item.get(xmi_type) == 'uml:Association'
        ]
        self.assertEqual(len(associations), 2)
        self.assertEqual({item.get('name') for item in associations}, {'created_by', 'updated_by'})
        self.assertEqual(
            len([item for item in root.find(f'{{{UMLDI}}}Diagram')
                 if item.get(xmi_type) == 'umldi:UMLEdge']),
            2,
        )

    def test_generator_maps_one_to_one_and_aggregation_without_cascade(self):
        usuario = self.node('usuario', 'entity')
        perfil = self.node('perfil', 'entity')
        catalogo = self.node('catalogo', 'entity')
        producto = self.node('producto', 'entity')
        for node in (usuario, perfil, catalogo, producto):
            node['data']['title'] = f"{node['id'].title()}.java"
        edges = [
            {
                **self.edge('usuario-perfil', 'usuario', 'perfil', 'asociacion'),
                'data': {
                    'relationType': 'asociacion', 'multiplicidadOrigen': '1',
                    'multiplicidadDestino': '1', 'ownerNodeId': 'usuario',
                    'sourceRole': 'perfil', 'targetRole': 'usuario', 'bidirectional': True,
                },
            },
            {
                **self.edge('catalogo-productos', 'catalogo', 'producto', 'agregacion'),
                'data': {
                    'relationType': 'agregacion', 'multiplicidadOrigen': '1',
                    'multiplicidadDestino': '*', 'ownerNodeId': 'catalogo',
                    'wholeNodeId': 'catalogo', 'sourceRole': 'productos',
                    'targetRole': 'catalogo', 'bidirectional': False,
                },
            },
        ]
        payload, _ = generate_spring_boot_zip(
            [usuario, perfil, catalogo, producto], edges,
            artifact='semantica', package='com.example.semantica',
        )
        with ZipFile(BytesIO(payload)) as archive:
            usuario_source = archive.read('semantica/src/main/java/com/example/semantica/entity/Usuario.java').decode()
            perfil_source = archive.read('semantica/src/main/java/com/example/semantica/entity/Perfil.java').decode()
            catalogo_source = archive.read('semantica/src/main/java/com/example/semantica/entity/Catalogo.java').decode()
        self.assertIn('@OneToOne', usuario_source)
        self.assertIn('@JoinColumn(name = "perfil_id")', usuario_source)
        self.assertIn('@OneToOne(mappedBy = "perfil")', perfil_source)
        self.assertIn('@OneToMany', catalogo_source)
        self.assertIn('List<Producto> productos', catalogo_source)
        self.assertNotIn('CascadeType.REMOVE', catalogo_source)
        self.assertNotIn('orphanRemoval = true', catalogo_source)

    def test_serializer_validates_enum_and_embedded_contract(self):
        entity = self.node('pedido', 'entity')
        entity['data'].update({
            'title': 'Pedido.java',
            'properties': [
                {'name': 'id', 'type': 'UUID (@Id)'},
                {'name': 'direccion', 'type': 'Direccion', 'embedded': True},
            ],
        })
        embeddable = self.node('direccion', 'embeddable')
        embeddable['data'].update({
            'title': 'Direccion.java',
            'properties': [{'name': 'calle', 'type': 'String'}],
        })
        enum = self.node('estado', 'enum')
        enum['data'].update({'title': 'Estado.java', 'literals': ['PENDIENTE']})
        valid = self.patch_document([entity, embeddable, enum], [])
        self.assertEqual(valid.status_code, 200)

        invalid_enum = self.node('estado-invalido', 'enum')
        invalid_enum['data']['literals'] = []
        invalid = self.patch_document([entity, embeddable, invalid_enum], [])
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('nodes', invalid.json())

        invalid_embedded = self.node('pedido-invalido', 'entity')
        invalid_embedded['data']['properties'].append({
            'name': 'texto', 'type': 'String', 'embedded': True,
        })
        invalid = self.patch_document([invalid_embedded], [])
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('nodes', invalid.json())

    def test_editor_cannot_generate_and_invalid_documents_return_errors(self):
        editor = get_user_model().objects.create_user(username='editor', password='clave-segura')
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=editor, rol=ProyectoMiembro.Rol.EDITOR
        )
        self.client.force_login(editor)
        denied = self.client.post(f'/api/diagramas/diagramas/{self.diagram.id}/generar-spring-boot/')
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.user)
        self.diagram.nodes = [self.node('pedido', 'entity'), self.node('cliente', 'entity')]
        self.diagram.edges = [self.edge('realiza', 'pedido', 'cliente', 'realizacion')]
        self.diagram.save()
        invalid = self.client.post(f'/api/diagramas/diagramas/{self.diagram.id}/generar-spring-boot/')
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('errors', invalid.json())
