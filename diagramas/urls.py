from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DiagramaViewSet, ClaseUMLViewSet, AtributoUMLViewSet,
    RelacionUMLViewSet, VersionDiagramaViewSet
)

router = DefaultRouter()
router.register(r'diagramas', DiagramaViewSet, basename='diagrama')
router.register(r'clases', ClaseUMLViewSet, basename='clase-uml')
router.register(r'atributos', AtributoUMLViewSet, basename='atributo-uml')
router.register(r'relaciones', RelacionUMLViewSet, basename='relacion-uml')
router.register(r'versiones', VersionDiagramaViewSet, basename='version-diagrama')

urlpatterns = [
    path('', include(router.urls)),
]
