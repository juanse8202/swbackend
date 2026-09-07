from django.contrib.auth import get_user_model
from django.test import Client, TestCase


class SessionLoginTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ana", email="ana@example.com", password="clave-segura"
        )

    def test_login_with_username_creates_a_django_session(self):
        client = Client(enforce_csrf_checks=True)
        csrf_response = client.get("/api/csrf/")
        response = client.post(
            "/api/login/", {"username": "ana", "password": "clave-segura"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrfToken"],
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("sessionid", response.cookies)

    def test_login_with_email_creates_a_django_session(self):
        client = Client(enforce_csrf_checks=True)
        csrf_response = client.get("/api/csrf/")
        response = client.post(
            "/api/login/", {"email": "ana@example.com", "password": "clave-segura"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrfToken"],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["id"], self.user.id)

    def test_api_requires_a_session(self):
        response = self.client.get("/api/proyectos/proyectos/")

        self.assertEqual(response.status_code, 403)

    def test_me_returns_the_authenticated_user(self):
        self.client.force_login(self.user)

        response = self.client.get("/api/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], "ana@example.com")


class RegistrationTests(TestCase):
    def test_registration_generates_unique_username_from_email(self):
        client = Client(enforce_csrf_checks=True)
        csrf_response = client.get("/api/csrf/")
        response = client.post(
            "/api/registro/",
            {"email": "juan@gmail.com", "password": "ClaveSegura123!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrfToken"],
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["user"]["username"], "juan")

        csrf_response = client.get("/api/csrf/")
        response = client.post(
            "/api/registro/",
            {"email": "juan@hotmail.com", "password": "ClaveSegura123!"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_response.json()["csrfToken"],
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["user"]["username"], "juan1")
