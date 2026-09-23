from django.contrib.auth import get_user_model
from django.test import TestCase
from io import BytesIO
from zipfile import ZipFile

from proyectos.models import Proyecto, ProyectoMiembro
from .models import Diagrama
from .spring_generator import generate_spring_boot_zip


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
