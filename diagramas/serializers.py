from rest_framework import serializers
from .models import Diagrama, ClaseUML, AtributoUML, RelacionUML, VersionDiagrama

class AtributoUMLSerializer(serializers.ModelSerializer):
    class Meta:
        model = AtributoUML
        fields = '__all__'

class ClaseUMLSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClaseUML
        fields = '__all__'

class RelacionUMLSerializer(serializers.ModelSerializer):
    class Meta:
        model = RelacionUML
        fields = '__all__'

class VersionDiagramaSerializer(serializers.ModelSerializer):
    class Meta:
        model = VersionDiagrama
        fields = '__all__'

class DiagramaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Diagrama
        fields = '__all__'