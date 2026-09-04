from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DiagramaViewSet, ClaseUMLViewSet, AtributoUMLViewSet,
    RelacionUMLViewSet, VersionDiagramaViewSet
)

router = DefaultRouter()
router.register(r'diagramas', DiagramaViewSet)
router.register(r'clases', ClaseUMLViewSet)
router.register(r'atributos', AtributoUMLViewSet)
router.register(r'relaciones', RelacionUMLViewSet)
router.register(r'versiones', VersionDiagramaViewSet)

urlpatterns = [
    path('', include(router.urls)),
]