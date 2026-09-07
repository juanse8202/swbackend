from django.db.models import Q
from rest_framework import viewsets
from .models import Proyecto
from .serializers import ProyectoSerializer, UserSerializer
from django.contrib.auth.models import User

class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

class ProyectoViewSet(viewsets.ModelViewSet):
    serializer_class = ProyectoSerializer

    def get_queryset(self):
        user = self.request.user
        return Proyecto.objects.filter(
            Q(creador=user) | Q(colaboradores=user)
        ).distinct()

    def perform_create(self, serializer):
        serializer.save(creador=self.request.user)
