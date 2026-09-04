from rest_framework import serializers
from .models import Proyecto
from django.contrib.auth.models import User

# Serializer para poder listar a los usuarios (colaboradores/creadores)
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']

class ProyectoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proyecto
        fields = '__all__'