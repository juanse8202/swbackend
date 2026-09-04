from rest_framework import viewsets
from .models import Diagrama, ClaseUML, AtributoUML, RelacionUML, VersionDiagrama
from .serializers import (
    DiagramaSerializer, ClaseUMLSerializer, AtributoUMLSerializer,
    RelacionUMLSerializer, VersionDiagramaSerializer
)

class DiagramaViewSet(viewsets.ModelViewSet):
    queryset = Diagrama.objects.all()
    serializer_class = DiagramaSerializer

class ClaseUMLViewSet(viewsets.ModelViewSet):
    queryset = ClaseUML.objects.all()
    serializer_class = ClaseUMLSerializer

class AtributoUMLViewSet(viewsets.ModelViewSet):
    queryset = AtributoUML.objects.all()
    serializer_class = AtributoUMLSerializer

class RelacionUMLViewSet(viewsets.ModelViewSet):
    queryset = RelacionUML.objects.all()
    serializer_class = RelacionUMLSerializer

class VersionDiagramaViewSet(viewsets.ModelViewSet):
    queryset = VersionDiagrama.objects.all()
    serializer_class = VersionDiagramaSerializer