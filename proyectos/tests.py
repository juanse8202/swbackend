from django.test import TestCase

from django.contrib.auth import get_user_model

from diagramas.models import Diagrama
from .models import Proyecto, ProyectoMiembro


class CollaborativeProjectTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.first_user = User.objects.create_user(
            username='ana', email='ana@example.com', password='clave-segura'
        )
        self.second_user = User.objects.create_user(
            username='beto', email='beto@example.com', password='clave-segura'
        )
        self.project = Proyecto.objects.create(nombre='Arquitectura', creador=self.second_user)
        self.second_diagram = Diagrama.objects.create(proyecto=self.project, nombre='Principal')

    def test_registration_creates_a_private_personal_workspace(self):
        self.client.force_login(self.first_user)
        response = self.client.get('/api/proyectos/proyectos/')
        self.assertEqual(response.status_code, 200)
        personal_projects = Proyecto.objects.filter(
            creador=self.first_user, nombre='Mi lienzo personal'
        )
        self.assertEqual(personal_projects.count(), 1)
        self.assertTrue(Diagrama.objects.filter(
            proyecto=personal_projects.get(), nombre='Diagrama principal'
        ).exists())
        personal_diagram = Diagrama.objects.get(proyecto=personal_projects.get())
        self.assertEqual(personal_diagram.clases.count(), 2)
        self.assertEqual(personal_diagram.relaciones.count(), 1)
        self.assertNotIn(self.second_diagram.id, [item['id'] for item in response.json()])

    def test_user_cannot_access_another_users_diagram(self):
        self.client.force_login(self.first_user)

        response = self.client.get(
            f'/api/diagramas/diagramas/{self.second_diagram.id}/'
        )

        self.assertEqual(response.status_code, 404)

    def test_diagrams_can_be_filtered_by_project(self):
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        own_project = Proyecto.objects.create(
            nombre='Proyecto propio', creador=self.first_user
        )
        own_diagram = Diagrama.objects.create(
            proyecto=own_project, nombre='Diagrama propio'
        )
        self.client.force_login(self.first_user)

        response = self.client.get(
            f'/api/diagramas/diagramas/?proyecto={self.project.id}'
        )

        self.assertEqual(response.status_code, 200)
        diagrams = response.json()
        self.assertEqual([diagram['id'] for diagram in diagrams], [self.second_diagram.id])
        self.assertEqual(diagrams[0]['proyecto'], self.project.id)
        self.assertNotIn(own_diagram.id, [diagram['id'] for diagram in diagrams])

    def test_creator_can_create_project_and_invite_collaborator(self):
        self.client.force_login(self.first_user)
        create_response = self.client.post(
            '/api/proyectos/proyectos/', {'nombre': 'Sistema de ventas'}, content_type='application/json'
        )
        self.assertEqual(create_response.status_code, 201)
        project_id = create_response.json()['id']
        self.assertTrue(Diagrama.objects.filter(proyecto_id=project_id, nombre='Diagrama principal').exists())

        invite_response = self.client.post(
            f'/api/proyectos/proyectos/{project_id}/invitar/',
            {'email': self.second_user.email, 'rol': 'arquitecto'}, content_type='application/json'
        )
        self.assertEqual(invite_response.status_code, 200)
        self.assertIn(self.second_user.id, invite_response.json()['colaboradores'])
        invited_member = next(
            member for member in invite_response.json()['miembros']
            if member['usuario']['id'] == self.second_user.id
        )
        self.assertEqual(invited_member['rol'], 'arquitecto')

        self.client.force_login(self.second_user)
        diagrams_response = self.client.get('/api/diagramas/diagramas/')
        self.assertEqual(diagrams_response.status_code, 200)
        self.assertTrue(any(item['proyecto'] == project_id for item in diagrams_response.json()))

    def test_reader_cannot_edit_diagram(self):
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.LECTOR
        )
        self.client.force_login(self.first_user)

        response = self.client.patch(
            f'/api/diagramas/diagramas/{self.second_diagram.id}/',
            {'nodes': []}, content_type='application/json'
        )

        self.assertEqual(response.status_code, 403)

    def test_owner_can_change_member_role(self):
        miembro = ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        self.client.force_login(self.second_user)

        response = self.client.patch(
            f'/api/proyectos/proyectos/{self.project.id}/miembros/{self.first_user.id}/',
            {'rol': 'lector'}, content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        miembro.refresh_from_db()
        self.assertEqual(miembro.rol, ProyectoMiembro.Rol.LECTOR)

    def test_collaborator_cannot_invite_another_user(self):
        third_user = get_user_model().objects.create_user(
            username='carla', email='carla@example.com', password='clave-segura'
        )
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        self.client.force_login(self.first_user)

        response = self.client.post(
            f'/api/proyectos/proyectos/{self.project.id}/invitar/',
            {'email': third_user.email}, content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)

    def test_diagram_nodes_and_edges_are_persisted_after_patch(self):
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.ARQUITECTO
        )
        self.client.force_login(self.first_user)
        nodes = [{'id': 'usuario', 'type': 'uml', 'position': {'x': 120, 'y': 80}, 'data': {'nombre': 'Usuario'}}]
        edges = [{'id': 'usuario-proyecto', 'source': 'usuario', 'target': 'proyecto'}]

        update_response = self.client.patch(
            f'/api/diagramas/diagramas/{self.second_diagram.id}/',
            {'nodes': nodes, 'edges': edges}, content_type='application/json'
        )
        self.assertEqual(update_response.status_code, 200)

        retrieve_response = self.client.get(f'/api/diagramas/diagramas/{self.second_diagram.id}/')
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(retrieve_response.json()['nodes'], nodes)
        self.assertEqual(retrieve_response.json()['edges'], edges)

# Create your tests here.
