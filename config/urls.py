"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from .authentication import (
    CsrfTokenView,
    CurrentUserView,
    LoginView,
    LogoutView,
    RegistroView,
)


urlpatterns = [
    path("admin/", admin.site.urls),

    # Sesion Django con nombre de usuario o correo.
    path("api/csrf/", CsrfTokenView.as_view(), name="csrf"),
    path("api/me/", CurrentUserView.as_view(), name="current-user"),
    path("api/login/", LoginView.as_view(), name="login"),
    path("api/registro/", RegistroView.as_view(), name="registro"),
    path("api/logout/", LogoutView.as_view(), name="logout"),
    path("accounts/", include("allauth.urls")),

    path(
        "api/proyectos/",
        include("proyectos.urls"),
    ),

    path(
        "api/diagramas/",
        include("diagramas.urls"),
    ),
]
