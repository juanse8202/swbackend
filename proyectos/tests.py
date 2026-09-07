from django.test import TestCase

from django.contrib.auth import get_user_model

from .services import get_default_diagram


class PrivateWorkspaceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.first_user = User.objects.create_user(
            username='ana', email='ana@example.com', password='clave-segura'
        )
        self.second_user = User.objects.create_user(
            username='beto', email='beto@example.com', password='clave-segura'
        )
        self.second_diagram = get_default_diagram(self.second_user)

    def test_each_user_gets_a_private_default_diagram(self):
        self.client.force_login(self.first_user)

        response = self.client.get('/api/mi-lienzo/')

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()['id'], self.second_diagram.id)

    def test_user_cannot_access_another_users_diagram(self):
        self.client.force_login(self.first_user)

        response = self.client.get(
            f'/api/diagramas/diagramas/{self.second_diagram.id}/'
        )

        self.assertEqual(response.status_code, 404)

# Create your tests here.
