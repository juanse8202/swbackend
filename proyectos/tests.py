from django.test import TestCase

from django.contrib.auth import get_user_model

from diagramas.models import ClaseUML, Diagrama, RelacionUML
from .models import InvitacionProyecto, Proyecto, ProyectoMiembro


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

    def test_listing_does_not_create_a_personal_workspace(self):
        self.client.force_login(self.first_user)
        response = self.client.get('/api/proyectos/proyectos/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertFalse(Proyecto.objects.filter(
            creador=self.first_user, nombre='Mi lienzo personal'
        ).exists())

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

    def test_project_list_includes_entity_and_relationship_totals(self):
        origen = ClaseUML.objects.create(diagrama=self.second_diagram, nombre='Usuario')
        destino = ClaseUML.objects.create(diagrama=self.second_diagram, nombre='Rol')
        RelacionUML.objects.create(
            diagrama=self.second_diagram, clase_origen=origen,
            clase_destino=destino, tipo='Asociacion',
        )
        self.client.force_login(self.second_user)

        response = self.client.get('/api/proyectos/proyectos/')

        self.assertEqual(response.status_code, 200)
        project = next(item for item in response.json() if item['id'] == self.project.id)
        self.assertEqual(project['total_entidades'], 2)
        self.assertEqual(project['total_relaciones'], 1)

    def test_project_list_counts_react_flow_nodes_and_edges(self):
        self.second_diagram.nodes = [{'id': str(index)} for index in range(3)]
        self.second_diagram.edges = [{'id': '1-2'}]
        self.second_diagram.save()
        self.client.force_login(self.second_user)

        response = self.client.get('/api/proyectos/proyectos/')

        project = next(item for item in response.json() if item['id'] == self.project.id)
        self.assertEqual(project['total_entidades'], 3)
        self.assertEqual(project['total_relaciones'], 1)

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
        self.assertEqual(invite_response.status_code, 201)
        invitation_id = invite_response.json()['id']
        self.assertEqual(invite_response.json()['estado'], 'pendiente')
        self.assertEqual(invite_response.json()['rol'], 'arquitecto')

        owner_projects = self.client.get('/api/proyectos/proyectos/').json()
        owner_project = next(project for project in owner_projects if project['id'] == project_id)
        self.assertEqual(owner_project['invitaciones_pendientes'], [{
            'id': invitation_id,
            'invitado': {
                'id': self.second_user.id,
                'username': self.second_user.username,
                'email': self.second_user.email,
            },
            'rol': 'arquitecto',
            'fecha': invite_response.json()['fecha'],
            'estado': 'pendiente',
        }])

        self.client.force_login(self.second_user)
        pending_response = self.client.get('/api/proyectos/invitaciones/pendientes/')
        self.assertEqual(pending_response.status_code, 200)
        self.assertEqual(pending_response.json()[0]['id'], invitation_id)
        accept_response = self.client.post(f'/api/proyectos/invitaciones/{invitation_id}/aceptar/')
        self.assertEqual(accept_response.status_code, 200)
        diagrams_response = self.client.get('/api/diagramas/diagramas/')
        self.assertEqual(diagrams_response.status_code, 200)
        self.assertTrue(any(item['proyecto'] == project_id for item in diagrams_response.json()))

    def test_creation_uses_the_authenticated_user_and_name_is_unique_per_owner(self):
        self.client.force_login(self.first_user)
        response = self.client.post(
            '/api/proyectos/proyectos/',
            # El campo de propietario se ignora aunque un cliente malicioso lo envíe.
            {'nombre': 'SIG', 'creador': self.second_user.id},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['propietario']['id'], self.first_user.id)
        self.assertEqual(Proyecto.objects.get(pk=response.json()['id']).creador, self.first_user)

        repeated = self.client.post(
            '/api/proyectos/proyectos/', {'nombre': 'sig'}, content_type='application/json'
        )
        self.assertEqual(repeated.status_code, 400)
        self.assertIn('nombre', repeated.json())

        self.client.force_login(self.second_user)
        other_owner = self.client.post(
            '/api/proyectos/proyectos/', {'nombre': 'SIG'}, content_type='application/json'
        )
        self.assertEqual(other_owner.status_code, 201)
        self.assertEqual(other_owner.json()['propietario']['id'], self.second_user.id)

    def test_projects_are_isolated_until_an_invitation_is_accepted(self):
        self.client.force_login(self.first_user)
        self.assertNotIn(
            self.project.id,
            [project['id'] for project in self.client.get('/api/proyectos/proyectos/').json()],
        )

        self.client.force_login(self.second_user)
        invite_response = self.client.post(
            f'/api/proyectos/proyectos/{self.project.id}/invitar/',
            {'usuario_id': self.first_user.id}, content_type='application/json',
        )
        invitation_id = invite_response.json()['id']
        self.client.force_login(self.first_user)
        self.client.post(f'/api/proyectos/invitaciones/{invitation_id}/aceptar/')
        self.assertIn(
            self.project.id,
            [project['id'] for project in self.client.get('/api/proyectos/proyectos/').json()],
        )

    def test_owner_can_resend_or_cancel_a_pending_invitation(self):
        self.client.force_login(self.second_user)
        invitation = self.client.post(
            f'/api/proyectos/proyectos/{self.project.id}/invitar/',
            {'usuario_id': self.first_user.id}, content_type='application/json',
        ).json()

        self.client.force_login(self.first_user)
        self.assertEqual(
            self.client.post(f"/api/proyectos/invitaciones/{invitation['id']}/reenviar/").status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"/api/proyectos/invitaciones/{invitation['id']}/").status_code,
            403,
        )

        self.client.force_login(self.second_user)
        resend = self.client.post(f"/api/proyectos/invitaciones/{invitation['id']}/reenviar/")
        self.assertEqual(resend.status_code, 200)
        self.assertEqual(resend.json()['estado'], 'pendiente')
        cancel = self.client.delete(f"/api/proyectos/invitaciones/{invitation['id']}/")
        self.assertEqual(cancel.status_code, 204)

        self.client.force_login(self.first_user)
        self.assertEqual(self.client.get('/api/proyectos/invitaciones/pendientes/').json(), [])

    def test_trash_is_private_and_collaborator_cannot_restore(self):
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        self.client.force_login(self.second_user)
        self.client.post(f'/api/proyectos/proyectos/{self.project.id}/archivar/')

        self.client.force_login(self.first_user)
        trash = self.client.get('/api/proyectos/proyectos/?archivados=true')
        self.assertEqual(trash.status_code, 200)
        self.assertEqual(trash.json(), [])
        restore = self.client.post(f'/api/proyectos/proyectos/{self.project.id}/restaurar/')
        self.assertEqual(restore.status_code, 403)

    def test_removed_collaborator_loses_list_and_diagram_write_access(self):
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        self.client.force_login(self.second_user)
        remove = self.client.delete(
            f'/api/proyectos/proyectos/{self.project.id}/colaboradores/{self.first_user.id}/'
        )
        self.assertEqual(remove.status_code, 204)

        self.client.force_login(self.first_user)
        projects = self.client.get('/api/proyectos/proyectos/')
        self.assertNotIn(self.project.id, [project['id'] for project in projects.json()])
        patch = self.client.patch(
            f'/api/diagramas/diagramas/{self.second_diagram.id}/',
            {'nodes': []}, content_type='application/json',
        )
        self.assertEqual(patch.status_code, 403)

    def test_collaborator_can_leave_but_owner_cannot_leave(self):
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        self.client.force_login(self.first_user)
        leave = self.client.delete(
            f'/api/proyectos/proyectos/{self.project.id}/colaboradores/{self.first_user.id}/'
        )
        self.assertEqual(leave.status_code, 204)
        self.assertFalse(ProyectoMiembro.objects.filter(
            proyecto=self.project, usuario=self.first_user
        ).exists())

        self.client.force_login(self.second_user)
        owner_leave = self.client.delete(
            f'/api/proyectos/proyectos/{self.project.id}/colaboradores/{self.second_user.id}/'
        )
        self.assertEqual(owner_leave.status_code, 400)

    def test_destroy_cascades_diagrams_members_and_invitations(self):
        invitation = InvitacionProyecto.objects.create(
            proyecto=self.project, invitador=self.second_user, invitado=self.first_user,
            rol=ProyectoMiembro.Rol.EDITOR,
        )
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        self.client.force_login(self.second_user)
        response = self.client.delete(f'/api/proyectos/proyectos/{self.project.id}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Proyecto.objects.filter(pk=self.project.id).exists())
        self.assertFalse(Diagrama.objects.filter(pk=self.second_diagram.id).exists())
        self.assertFalse(ProyectoMiembro.objects.filter(proyecto_id=self.project.id).exists())
        self.assertFalse(InvitacionProyecto.objects.filter(pk=invitation.id).exists())

    def test_archive_duplicate_and_filters(self):
        self.client.force_login(self.second_user)
        duplicate_response = self.client.post(
            f'/api/proyectos/proyectos/{self.project.id}/duplicar/'
        )
        self.assertEqual(duplicate_response.status_code, 201)
        copy_id = duplicate_response.json()['id']
        self.assertEqual(Diagrama.objects.filter(proyecto_id=copy_id).count(), 1)

        archive_response = self.client.post(
            f'/api/proyectos/proyectos/{self.project.id}/archivar/'
        )
        self.assertEqual(archive_response.status_code, 200)
        self.assertTrue(archive_response.json()['archivado'])
        trash_response = self.client.get('/api/proyectos/proyectos/?archivados=true')
        self.assertEqual([project['id'] for project in trash_response.json()], [self.project.id])

        restore_response = self.client.post(
            f'/api/proyectos/proyectos/{self.project.id}/restaurar/'
        )
        self.assertEqual(restore_response.status_code, 200)
        self.assertFalse(restore_response.json()['archivado'])
        self.assertTrue(Proyecto.objects.get(pk=self.project.id).archivado is False)

    def test_restore_does_not_restore_former_collaborators(self):
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        self.client.force_login(self.second_user)
        self.assertEqual(
            self.client.post(f'/api/proyectos/proyectos/{self.project.id}/archivar/').status_code,
            200,
        )
        self.assertFalse(ProyectoMiembro.objects.filter(
            proyecto=self.project, usuario=self.first_user
        ).exists())
        self.assertEqual(
            self.client.post(f'/api/proyectos/proyectos/{self.project.id}/restaurar/').status_code,
            200,
        )

        self.client.force_login(self.first_user)
        projects = self.client.get('/api/proyectos/proyectos/').json()
        self.assertNotIn(self.project.id, [project['id'] for project in projects])

    def test_restore_cleans_collaborators_from_legacy_archived_project(self):
        ProyectoMiembro.objects.create(
            proyecto=self.project, usuario=self.first_user, rol=ProyectoMiembro.Rol.EDITOR
        )
        # Simula un proyecto archivado por una versión anterior del backend.
        Proyecto.objects.filter(pk=self.project.id).update(archivado=True)
        self.client.force_login(self.second_user)

        response = self.client.post(f'/api/proyectos/proyectos/{self.project.id}/restaurar/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProyectoMiembro.objects.filter(
            proyecto=self.project, usuario=self.first_user
        ).exists())

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
        nodes = [
            {'id': 'usuario', 'type': 'uml', 'position': {'x': 120, 'y': 80}, 'data': {'nombre': 'Usuario'}},
            {'id': 'proyecto', 'type': 'uml', 'position': {'x': 360, 'y': 80}, 'data': {'nombre': 'Proyecto'}},
        ]
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
