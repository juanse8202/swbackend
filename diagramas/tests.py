from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from io import BytesIO
from zipfile import ZipFile
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync

from lxml import etree
from django.utils import timezone

from proyectos.models import Proyecto, ProyectoMiembro
from proyectos.services import create_project_with_main_diagram
from .models import Diagrama, PlanIA
from .ai_operations import OperationError, execute_operations
from .ai_interpreter import AiProviderError
from .ai_plans import create_plan
from .consumers import save_diagram_if_user_can_access
from .spring_generator import DiagramGenerationError, generate_spring_boot_zip
from .foreign_keys import normalize_legacy_foreign_keys
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

    def patch_document(self, nodes, edges, *, expected_revision=None):
        self.diagram.refresh_from_db(fields=['revision'])
        return self.client.patch(
            f'/api/diagramas/diagramas/{self.diagram.id}/',
            {
                'nodes': nodes, 'edges': edges,
                'expected_revision': self.diagram.revision if expected_revision is None else expected_revision,
            },
            content_type='application/json',
        )

    def test_document_writes_require_matching_expected_revision(self):
        nodes = [self.node('cliente', 'entity')]
        missing = self.client.patch(
            f'/api/diagramas/diagramas/{self.diagram.id}/',
            {'nodes': nodes, 'edges': []}, content_type='application/json',
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()['code'], 'expected_revision_required')

        saved = self.patch_document(nodes, [])
        self.assertEqual(saved.status_code, 200)
        stale = self.patch_document(nodes, [], expected_revision=0)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['code'], 'stale_revision')
        self.assertEqual(stale.json()['current_revision'], 1)

    def test_websocket_save_rejects_stale_snapshot_without_overwrite(self):
        first_nodes = [self.node('cliente', 'entity')]
        first = async_to_sync(save_diagram_if_user_can_access)(
            self.user.id, self.diagram.id, first_nodes, [], 0,
        )
        self.assertEqual(first['status'], 'saved')
        stale = async_to_sync(save_diagram_if_user_can_access)(
            self.user.id, self.diagram.id, [self.node('pedido', 'entity')], [], 0,
        )
        self.assertEqual(stale['status'], 'stale_revision')
        self.assertEqual(stale['error']['code'], 'stale_revision')
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.nodes, first_nodes)

    def test_uml_association_between_classes_is_compatible_without_jpa_flag(self):
        """Old and EA-style UML associations must not be reinterpreted as JPA."""
        nodes = [self.node('cliente', 'class'), self.node('pedido', 'class')]
        edge = self.edge('cliente-pedido', 'cliente', 'pedido', 'asociacion')
        response = self.patch_document(nodes, [edge])
        self.assertEqual(response.status_code, 200, response.content)
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.edges, [edge])

        archive, _filename = generate_spring_boot_zip(nodes, [edge])
        with ZipFile(BytesIO(archive)) as generated:
            names = set(generated.namelist())
        self.assertIn('diagramcraft-generated/src/main/java/com/diagramcraft/generated/model/Cliente.java', names)
        self.assertIn('diagramcraft-generated/src/main/java/com/diagramcraft/generated/model/Pedido.java', names)
        self.assertNotIn('diagramcraft-generated/src/main/java/com/diagramcraft/generated/repository/ClienteRepository.java', names)

    def test_jpa_flag_requires_entities_but_uml_flag_is_valid(self):
        nodes = [self.node('cliente', 'class'), self.node('pedido', 'entity')]
        edge = self.edge('cliente-pedido', 'cliente', 'pedido', 'asociacion')
        edge['data']['jpaManaged'] = False
        self.assertEqual(self.patch_document(nodes, [edge]).status_code, 200)

        edge['data']['jpaManaged'] = True
        response = self.patch_document(nodes, [edge])
        self.assertEqual(response.status_code, 400)
        self.assertIn('solo pueden unir entidades', str(response.json()))
        with self.assertRaises(DiagramGenerationError) as caught:
            generate_spring_boot_zip(nodes, [edge])
        self.assertEqual(caught.exception.errors[0]['code'], 'invalid_jpa_relationship')

    def test_explicit_uml_flag_skips_jpa_mapping_between_entities(self):
        nodes = [self.node('cliente', 'entity'), self.node('pedido', 'entity')]
        edge = self.edge('cliente-pedido', 'cliente', 'pedido', 'asociacion')
        edge['data']['jpaManaged'] = False
        self.assertEqual(self.patch_document(nodes, [edge]).status_code, 200)
        archive, _filename = generate_spring_boot_zip(nodes, [edge])
        with ZipFile(BytesIO(archive)) as generated:
            source = generated.read(
                'diagramcraft-generated/src/main/java/com/diagramcraft/generated/entity/Cliente.java'
            ).decode()
        self.assertIn('@Entity', source)
        self.assertNotIn('@OneToOne', source)
        self.assertNotIn('private Pedido', source)

    def test_invalid_jpa_flag_is_rejected_by_save_and_generation(self):
        nodes = [self.node('cliente', 'class'), self.node('pedido', 'class')]
        for value in ('false', 0, 1, [], {}):
            with self.subTest(value=value):
                edge = self.edge('cliente-pedido', 'cliente', 'pedido', 'asociacion')
                edge['data']['jpaManaged'] = value
                response = self.patch_document(nodes, [edge])
                self.assertEqual(response.status_code, 400)
                self.assertIn('jpaManaged', str(response.json()))
                with self.assertRaises(DiagramGenerationError) as caught:
                    generate_spring_boot_zip(nodes, [edge])
                self.assertEqual(caught.exception.errors[0]['code'], 'invalid_jpa_managed')

    def test_legacy_sql_fk_is_normalized_to_a_managed_relationship(self):
        user = self.node('auth-user', 'entity')
        user['data']['title'] = 'auth_user.java'
        audit = self.node('audit', 'entity')
        audit['data']['properties'].append({
            'name': 'created_by(FK: auth_user)', 'type': 'String',
        })
        nodes, edges, warnings = normalize_legacy_foreign_keys([audit, user], [])
        property_ = nodes[0]['data']['properties'][1]
        self.assertEqual(property_['name'], 'created_by')
        self.assertEqual(property_['sourceLabel'], 'created_by(FK: auth_user)')
        self.assertEqual(property_['foreignTable'], 'auth_user')
        self.assertEqual(property_['foreignKeyMode'], 'relation')
        self.assertFalse(warnings)
        self.assertEqual(len(edges), 1)
        self.assertTrue(edges[0]['data']['jpaManaged'])

        archive, _filename = generate_spring_boot_zip([audit, user], [])
        with ZipFile(BytesIO(archive)) as generated:
            source = generated.read(
                'diagramcraft-generated/src/main/java/com/diagramcraft/generated/entity/Audit.java'
            ).decode()
        self.assertIn('@ManyToOne', source)
        self.assertIn('private Auth_user created_by;', source)
        self.assertNotIn('private String created_by;', source)

    def test_legacy_sql_fk_with_semicolon_from_xmi_is_normalized(self):
        user = self.node('auth-user', 'class')
        user['data']['title'] = 'auth_user.java'
        audit = self.node('audit', 'class')
        audit['data']['properties'].append({
            'name': 'created_by(FK; auth_user)}', 'type': 'String',
        })

        nodes, edges, warnings = normalize_legacy_foreign_keys([audit, user], [])

        property_ = nodes[0]['data']['properties'][0]
        self.assertEqual(property_['name'], 'created_by')
        self.assertEqual(property_['sourceLabel'], 'created_by(FK; auth_user)}')
        self.assertFalse(edges[0]['data']['jpaManaged'])
        self.assertFalse(warnings)

    def test_legacy_fk_to_class_is_uml_and_unresolved_fk_is_a_warning(self):
        provider = self.node('proveedor', 'class')
        invoice = self.node('factura', 'entity')
        invoice['data']['properties'].append({'name': 'proveedor(FK: proveedor)', 'type': 'String'})
        nodes, edges, warnings = normalize_legacy_foreign_keys([invoice, provider], [])
        self.assertFalse(edges[0]['data']['jpaManaged'])
        self.assertFalse(warnings)
        archive, _filename = generate_spring_boot_zip(nodes, edges)
        self.assertTrue(archive)

        orphan = self.node('orden', 'entity')
        orphan['data']['properties'].append({'name': 'sucursal(FK: sucursal)', 'type': 'String'})
        archive, _filename, warnings = generate_spring_boot_zip([orphan], [], return_warnings=True)
        self.assertTrue(archive)
        self.assertEqual(warnings[0]['code'], 'unresolved_legacy_foreign_key')

    def test_legacy_fk_collision_is_disambiguated_and_generated_java_compiles(self):
        user = self.node('usuario', 'class')
        user['data']['title'] = 'usuario.java'
        sale = self.node('venta', 'class')
        sale['data']['properties'] = [
            {'name': 'deleted_at', 'type': 'Integer'},
            {'name': 'deleted_at(FK; usuario)', 'type': 'Integer'},
        ]
        nodes, edges, warnings = normalize_legacy_foreign_keys([sale, user], [])
        self.assertEqual([item['name'] for item in nodes[0]['data']['properties']], ['deleted_at', 'deleted_at_id'])
        self.assertEqual(edges[0]['data']['sourceRole'], 'deleted_at_id')
        self.assertEqual(warnings[0]['code'], 'legacy_foreign_key_name_disambiguated')
        archive, _filename = generate_spring_boot_zip(nodes, edges)
        with ZipFile(BytesIO(archive)) as generated:
            source = generated.read('diagramcraft-generated/src/main/java/com/diagramcraft/generated/model/Venta.java').decode()
        self.assertIn('private Integer deleted_at;', source)
        self.assertIn('private Integer deleted_at_id;', source)

    def test_previously_saved_legacy_fk_collision_is_disambiguated_at_generation(self):
        sale = self.node('venta', 'class')
        sale['data']['properties'] = [
            {'name': 'deleted_at', 'type': 'Integer'},
            {'name': 'deleted_at', 'type': 'Integer', 'sourceLabel': 'deleted_at(FK; auth_user)', 'foreignKeyMode': 'scalar'},
        ]
        archive, _filename = generate_spring_boot_zip([sale], [])
        with ZipFile(BytesIO(archive)) as generated:
            source = generated.read('diagramcraft-generated/src/main/java/com/diagramcraft/generated/model/Venta.java').decode()
        self.assertIn('private Integer deleted_at_id;', source)

    def test_manual_invalid_property_is_not_silently_normalized(self):
        node = self.node('pedido', 'entity')
        node['data']['properties'].append({'name': 'campo invalido!', 'type': 'String'})
        with self.assertRaises(DiagramGenerationError) as caught:
            generate_spring_boot_zip([node], [])
        self.assertEqual(caught.exception.errors[0]['code'], 'invalid_java_identifier')

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
        self.assertEqual(response.json()['destructive_impact'], {'nodes': [], 'edges': []})
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
        self.assertEqual(failure_response.json(), {
            'code': 'ai_interpretation_failed',
            'message': 'network',
            'target': None,
            'current_revision': 0,
        })
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

    def test_ai_project_create_requires_confirmation_and_returns_authoritative_project(self):
        operation = {
            'id': '4a8cfb73-84d6-4f5d-bd4a-3e7fddd23412',
            'op': 'project.create',
            'payload': {'name': 'Sistema de Ventas', 'create_main_diagram': True},
        }
        plan = create_plan(user=self.user, diagram=self.diagram, operations=[operation])
        payload = {'plan_id': str(plan.plan_id), 'idempotency_key': 'create-sales-project', 'confirm': False}
        denied = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/', payload,
            content_type='application/json',
        )
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.json()['code'], 'confirmation_required')

        payload['confirm'] = True
        created = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/', payload,
            content_type='application/json',
        )
        self.assertEqual(created.status_code, 200)
        result = created.json()
        self.assertEqual(result['summary'], 'Proyecto Sistema de Ventas creado.')
        self.assertEqual(result['project']['nombre'], 'Sistema de Ventas')
        self.assertEqual(result['diagram']['nodes'], [])
        self.assertEqual(result['diagram']['edges'], [])
        self.assertTrue(ProyectoMiembro.objects.filter(
            proyecto_id=result['project']['id'], usuario=self.user,
            rol=ProyectoMiembro.Rol.PROPIETARIO,
        ).exists())
        retry = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/', payload,
            content_type='application/json',
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json(), result)

    def test_ai_interprets_dynamic_project_names_and_clarifies_missing_name(self):
        for instruction, name in (
            ('Crea un proyecto llamado Sistema de Ventas', 'Sistema de Ventas'),
            ('Diana, crea un proyecto Biblioteca Digital', 'Biblioteca Digital'),
            ('Quiero crear un proyecto Hospital Central', 'Hospital Central'),
        ):
            with self.subTest(instruction=instruction):
                response = self.client.post(
                    f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
                    {'instruction': instruction, 'request_id': f'project-{name}'},
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 200)
                operation = response.json()['operations'][0]
                self.assertEqual(operation['op'], 'project.create')
                self.assertEqual(operation['payload']['name'], name)
                self.assertTrue(operation['payload']['create_main_diagram'])

        clarification = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
            {'instruction': 'Crea un proyecto'}, content_type='application/json',
        )
        self.assertEqual(clarification.status_code, 200)
        self.assertEqual(clarification.json(), {
            'status': 'clarification', 'summary': '',
            'question': '¿Qué nombre deseas para el proyecto?',
            'message': '¿Qué nombre deseas para el proyecto?',
            'candidates': [], 'operations': [], 'request_id': None,
        })

    def test_ai_interprets_class_entity_interface_and_attributes_without_provider(self):
        cases = (
            ('Diana, crea una clase llamada Persona', 'class', 'Persona', []),
            ('Crea una clase Persona con atributo nombre de tipo String', 'class', 'Persona', [('nombre', 'String')]),
            ('Crea una clase Producto con codigo String, nombre String y precio Decimal', 'class', 'Producto', [('codigo', 'String'), ('nombre', 'String'), ('precio', 'Decimal')]),
            ('Crea una entidad Cliente con nombre String y correo String', 'entity', 'Cliente', [('nombre', 'String'), ('correo', 'String')]),
            ('Diana, crea una interfaz llamada Pagable', 'interface', 'Pagable', []),
            ('Crea una clase Factura con fecha LocalDate y total BigDecimal', 'class', 'Factura', [('fecha', 'LocalDate'), ('total', 'BigDecimal')]),
        )
        unavailable_provider = FakeAiProvider(error=AiProviderError('Gemini no configurado'))
        for instruction, kind, title, attributes in cases:
            with self.subTest(instruction=instruction):
                with patch('diagramas.views.get_ai_provider', return_value=unavailable_provider):
                    response = self.client.post(
                        f'/api/diagramas/diagramas/{self.diagram.id}/interpretar-ia/',
                        {'instruction': instruction}, content_type='application/json',
                    )
                self.assertEqual(response.status_code, 200, response.content)
                body = response.json()
                self.assertEqual(body['status'], 'ready')
                self.assertIn('plan_id', body)
                operation = body['operations'][0]
                self.assertEqual((operation['kind'], operation['title']), (kind, title))
                self.assertEqual([(item['name'], item['type']) for item in operation['properties']], attributes)
                self.assertTrue(body['requires_confirmation'])
        self.assertEqual(unavailable_provider.contexts, [])

    def test_ai_project_create_rejects_duplicate_active_name(self):
        Proyecto.objects.create(nombre='Biblioteca Digital', creador=self.user)
        plan = create_plan(user=self.user, diagram=self.diagram, operations=[{
            'id': '72a8f91d-4d4c-4f0e-8cc9-7a99136df45b', 'op': 'project.create',
            'payload': {'name': 'Biblioteca Digital', 'create_main_diagram': True},
        }])
        response = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/aplicar-plan-ia/',
            {'plan_id': str(plan.plan_id), 'idempotency_key': 'duplicate-project', 'confirm': True},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'duplicate_project_name')
        self.assertIsNone(response.json()['target'])
        self.assertIsNone(response.json()['current_revision'])

    def test_project_service_rolls_back_when_main_diagram_creation_fails(self):
        before = Proyecto.objects.filter(creador=self.user).count()
        with patch('diagramas.models.Diagrama.objects.create', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                create_project_with_main_diagram(creator=self.user, nombre='No Persistir')
        self.assertEqual(Proyecto.objects.filter(creador=self.user).count(), before)

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
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()['code'], 'stale_revision')
        self.assertEqual(conflict.json()['current_revision'], self.diagram.revision)

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
        self.assertEqual(invalid.json()['code'], 'invalid_plan')
        self.assertEqual(invalid.json()['target'], {'kind': 'node', 'id': 'missing'})
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
            self.assertIn('ventas/.env.example', names)
            self.assertIn('ventas/.gitignore', names)
            self.assertIn('ventas/src/main/java/com/example/ventas/model/Notificable.java', names)
            self.assertIn('ventas/src/main/resources/application.properties', names)
            persona = archive.read('ventas/src/main/java/com/example/ventas/entity/Persona.java').decode()
            client = archive.read('ventas/src/main/java/com/example/ventas/entity/Cliente.java').decode()
            controller = archive.read('ventas/src/main/java/com/example/ventas/controller/PersonaController.java').decode()
            service = archive.read('ventas/src/main/java/com/example/ventas/service/PersonaService.java').decode()
            mapper = archive.read('ventas/src/main/java/com/example/ventas/mapper/PersonaMapper.java').decode()
            properties = archive.read('ventas/src/main/resources/application.properties').decode()
            env_example = archive.read('ventas/.env.example').decode()
            gitignore = archive.read('ventas/.gitignore').decode()
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
        self.assertIn('spring.config.import=optional:file:.env[.properties]', properties)
        self.assertIn('DB_URL=jdbc:postgresql://localhost:5432/app', env_example)
        self.assertIn('Copy-Item -LiteralPath ".env.example" -Destination ".env"', env_example)
        self.assertNotIn('DB_PASSWORD=tu_clave', env_example)
        self.assertIn('.env', gitignore)
        self.assertNotIn('ventas/.env', names)

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
        )
        self.assertTrue(package.get(f'{{{XMI}}}id').startswith('EAPK_'))
        self.assertEqual(package.get('name'), 'DiagramCraft')
        self.assertEqual(diagram.get('modelElement'), package.get(f'{{{XMI}}}id'))
        extension = root.find(f'{{{XMI}}}Extension')
        self.assertIsNotNone(extension)
        ea_diagram = extension.find('diagrams/diagram')
        self.assertIsNotNone(ea_diagram)
        self.assertEqual(ea_diagram.get(f'{{{XMI}}}id'), diagram.get(f'{{{XMI}}}id'))
        self.assertEqual(ea_diagram.find('model').get('owner'), package.get(f'{{{XMI}}}id'))
        package_extension = extension.find('elements/element')
        self.assertEqual(
            package_extension.find('model').get('package2'),
            package.get(f'{{{XMI}}}id').replace('EAPK_', 'EAID_', 1),
        )
        self.assertEqual(ea_diagram.find('properties').get('name'), 'DiagramCraft')
        self.assertEqual(len(ea_diagram.find('elements')), 3 + 6)
        self.assertEqual(len(extension.find('elements')), 1 + 3)
        connectors = extension.find('connectors')
        self.assertEqual(len(connectors), 6)
        self.assertEqual(
            {connector.get(f'{{{XMI}}}idref') for connector in connectors},
            {edge.get('modelElement') for edge in diagram if edge.get(xmi_type) == 'umldi:UMLEdge'},
        )
        self.assertTrue(all(
            connector.find('source') is not None and connector.find('target') is not None
            for connector in connectors
        ))
        xmi_ids = [
            item.get(f'{{{XMI}}}id') for item in root.iter()
            if item.get(f'{{{XMI}}}id')
        ]
        self.assertEqual(
            {value for value in xmi_ids if xmi_ids.count(value) > 1},
            {diagram.get(f'{{{XMI}}}id')},
        )
        self.assertTrue(diagram.get(f'{{{XMI}}}id').startswith('EAID_'))
        shapes = [
            item for item in diagram
            if item.get(xmi_type) == 'umldi:UMLClassifierShape'
        ]
        self.assertEqual(len(shapes), 3)
        classifier_ids = {
            item.get(f'{{{XMI}}}id') for item in root.iter()
            if item.get(xmi_type) in {'uml:Class', 'uml:Interface'}
            and item.get(f'{{{XMI}}}id')
        }
        self.assertEqual(
            {shape.get('modelElement') for shape in shapes},
            classifier_ids,
        )
        self.assertTrue(all(value.startswith('EAID_') for value in classifier_ids))
        self.assertNotIn(b'EAID_existing_source', exported)
        self.assertNotIn(b'EAID_existing_target', exported)
        self.assertTrue(all(shape.find('bounds') is not None for shape in shapes))
        source_shape = next(shape for shape in shapes if shape.find('bounds').get('x') == '120')
        self.assertEqual(source_shape.find('bounds').get('x'), '120')
        self.assertEqual(source_shape.find('bounds').get('y'), '240')

        diagram_edges = [
            item for item in diagram if item.get(xmi_type) == 'umldi:UMLEdge'
        ]
        self.assertEqual(len(diagram_edges), 6)
        self.assertEqual(
            {edge.get('modelElement') for edge in diagram_edges},
            {connector.get(f'{{{XMI}}}idref') for connector in connectors},
        )
        self.assertTrue(all(len(edge.findall('waypoint')) == 2 for edge in diagram_edges))
        diagram_element_styles = [item.get('style', '') for item in ea_diagram.find('elements')]
        self.assertEqual(sum('DUID=' in style for style in diagram_element_styles), 3)
        self.assertEqual(sum('SOID=' in style and 'EOID=' in style for style in diagram_element_styles), 6)

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

        association_edge = next(edge for edge in diagram_edges if edge.get('modelElement') == association.get(f'{{{XMI}}}id'))
        self.assertFalse(any(item.get(xmi_type) == 'umldi:UMLMultiplicityLabel'
                             for item in association_edge))
        aggregation_edge = next(edge for edge in diagram_edges if edge.get('modelElement') == aggregation.get(f'{{{XMI}}}id'))
        multiplicity_labels = [
            item for item in aggregation_edge
            if item.get(xmi_type) == 'umldi:UMLMultiplicityLabel'
        ]
        self.assertEqual({item.get('text') for item in multiplicity_labels}, {'1', '0..*'})
        self.assertEqual(
            {item.get('modelElement') for item in multiplicity_labels},
            {item.get(f'{{{XMI}}}id') for item in aggregation_ends},
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
        self.assertEqual(imported_aggregation['data']['multiplicidadDestino'], '0..*')

    def test_xmi_import_uses_ea_umldi_positions_and_export_names_the_diagram(self):
        source = b'''<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/spec/XMI/20131001" xmlns:uml="http://www.omg.org/spec/UML/20131001" xmlns:umldi="http://www.omg.org/spec/UML/20131001/UMLDI" xmlns:dc="http://www.omg.org/spec/UML/20131001/UMLDC" xmi:version="2.5"><xmi:Documentation exporter="Enterprise Architect"/><uml:Model xmi:id="m"><packagedElement xmi:type="uml:Class" xmi:id="cliente" name="Cliente"/></uml:Model><umldi:Diagram xmi:type="umldi:UMLClassDiagram" xmi:id="d" modelElement="m"><ownedElement xmi:type="umldi:UMLClassifierShape" xmi:id="s" modelElement="cliente"><bounds xmi:type="dc:bounds" x="320" y="180" width="260" height="140"/></ownedElement></umldi:Diagram></xmi:XMI>'''
        imported = parse_xmi(source)
        self.assertEqual(imported['nodes'][0]['position'], {'x': 320, 'y': 180})
        self.assertEqual(imported['nodes'][0]['width'], 260)
        self.assertEqual(imported['nodes'][0]['height'], 140)

        xml = export_xmi(imported['nodes'], imported['edges'], diagram_name='Finanzas')
        root = etree.fromstring(xml)
        self.assertEqual(root.find(f'{{{UMLDI}}}Diagram').get('name'), 'Finanzas')
        self.assertEqual(
            root.find(f'{{{XMI}}}Extension').find('diagrams/diagram/properties').get('name'),
            'Finanzas',
        )

    def test_xmi_import_keeps_ea_connector_route_and_label_positions(self):
        source = b'''<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/spec/XMI/20131001" xmlns:uml="http://www.omg.org/spec/UML/20131001" xmlns:umldi="http://www.omg.org/spec/UML/20131001/UMLDI" xmlns:dc="http://www.omg.org/spec/UML/20131001/UMLDC" xmi:version="2.5"><xmi:Documentation exporter="Enterprise Architect"/><uml:Model xmi:id="m"><packagedElement xmi:type="uml:Class" xmi:id="a" name="A"/><packagedElement xmi:type="uml:Class" xmi:id="b" name="B"/><packagedElement xmi:type="uml:Association" xmi:id="r" name="uses"><ownedEnd xmi:id="ea" type="a" lower="1" upper="1"/><ownedEnd xmi:id="eb" type="b" lower="0" upper="*"/></packagedElement></uml:Model><umldi:Diagram xmi:type="umldi:UMLClassDiagram" xmi:id="d" modelElement="m"><ownedElement xmi:type="umldi:UMLEdge" xmi:id="dr" source="a" target="b" modelElement="r"><ownedElement xmi:type="umldi:UMLMultiplicityLabel" text="1" modelElement="ea"><bounds xmi:type="dc:bounds" x="101" y="102"/></ownedElement><ownedElement xmi:type="umldi:UMLMultiplicityLabel" text="0..*" modelElement="eb"><bounds xmi:type="dc:bounds" x="301" y="302"/></ownedElement><ownedElement xmi:type="umldi:UMLNameLabel" text="uses"><bounds xmi:type="dc:bounds" x="201" y="202"/></ownedElement><waypoint xmi:type="dc:waypoint" x="100" y="100"/><waypoint xmi:type="dc:waypoint" x="200" y="150"/><waypoint xmi:type="dc:waypoint" x="300" y="300"/></ownedElement></umldi:Diagram></xmi:XMI>'''
        imported = parse_xmi(source)
        data = imported['edges'][0]['data']
        self.assertEqual(data['xmiWaypoints'], [{'x': 100, 'y': 100}, {'x': 200, 'y': 150}, {'x': 300, 'y': 300}])
        self.assertEqual(data['xmiLabelPositions'], {'source': {'x': 101, 'y': 102}, 'target': {'x': 301, 'y': 302}, 'name': {'x': 201, 'y': 202}})
        self.assertEqual(data['multiplicidadDestino'], '0..*')

    def test_xmi_import_normalizes_ea_java_primitive_ids_before_generation(self):
        source = b'''<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/spec/XMI/20131001" xmlns:uml="http://www.omg.org/spec/UML/20131001" xmi:version="2.5"><xmi:Documentation exporter="Enterprise Architect"/><uml:Model xmi:id="m"><packagedElement xmi:type="uml:Class" xmi:id="saldo" name="Saldo"><ownedAttribute xmi:type="uml:Property" name="monto"><type xmi:idref="EAJava_int"/></ownedAttribute></packagedElement><packagedElement xmi:type="uml:PrimitiveType" xmi:id="EAJava_int" name="int"/></uml:Model></xmi:XMI>'''
        imported = parse_xmi(source)
        self.assertEqual(imported['nodes'][0]['data']['properties'][0]['type'], 'Integer')
        archive, _ = generate_spring_boot_zip(imported['nodes'], imported['edges'])
        with ZipFile(BytesIO(archive)) as generated:
            java = generated.read('diagramcraft-generated/src/main/java/com/diagramcraft/generated/model/Saldo.java').decode()
        self.assertIn('private Integer monto;', java)
        self.assertNotIn('EAJava_int', java)

    def test_xmi_import_normalizes_sql_attribute_captions(self):
        source = b'''<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/spec/XMI/20131001" xmlns:uml="http://www.omg.org/spec/UML/20131001" xmi:version="2.5"><xmi:Documentation exporter="Enterprise Architect"/><uml:Model xmi:id="m"><packagedElement xmi:type="uml:Class" xmi:id="servicio" name="servicio"><ownedAttribute xmi:type="uml:Property" name="porcentaje de bonificacion"/><ownedAttribute xmi:type="uml:Property" name="tipo(FK; tipo_servicio)"/></packagedElement><packagedElement xmi:type="uml:Class" xmi:id="tipo_servicio" name="tipo_servicio"/></uml:Model></xmi:XMI>'''
        imported = parse_xmi(source)
        attributes = imported['nodes'][0]['data']['properties']
        self.assertEqual(attributes[0]['name'], 'porcentajeDeBonificacion')
        self.assertEqual(attributes[0]['sourceLabel'], 'porcentaje de bonificacion')
        self.assertEqual(attributes[1]['name'], 'tipo')
        self.assertEqual(attributes[1]['foreignTable'], 'tipo_servicio')
        self.assertFalse(any(edge['data'].get('legacyForeignKey') for edge in imported['edges']))

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

    def test_canvas_can_save_incomplete_entity_while_generator_reports_identifier(self):
        draft = self.node('borrador', 'entity')
        draft['data']['properties'] = []
        saved = self.patch_document([draft], [])
        self.assertEqual(saved.status_code, 200)

        response = self.client.post(
            f'/api/diagramas/diagramas/{self.diagram.id}/generar-spring-boot/',
            {'artifact': 'borrador', 'package': 'com.example.borrador'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errors'][0]['code'], 'entity_identifier_required')

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
