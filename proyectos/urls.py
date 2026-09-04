from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProyectoViewSet, UserViewSet

router = DefaultRouter()
router.register(r'proyectos', ProyectoViewSet)
router.register(r'usuarios', UserViewSet)

urlpatterns = [
    path('', include(router.urls)),
]