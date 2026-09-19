from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InvitacionProyectoViewSet, ProyectoViewSet, UserViewSet

router = DefaultRouter()
router.register(r'proyectos', ProyectoViewSet, basename='proyecto')
router.register(r'usuarios', UserViewSet)
router.register(r'invitaciones', InvitacionProyectoViewSet, basename='invitacion')

urlpatterns = [
    path(
        'invitaciones/pendientes/',
        InvitacionProyectoViewSet.as_view({'get': 'list'}),
        name='invitacion-pendiente-list',
    ),
    path('', include(router.urls)),
]
