from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Count, Func, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import InvitacionProyecto, Proyecto, ProyectoMiembro
from .permissions import require_owner
from .realtime import notify_invitation_accepted
from .serializers import InvitacionProyectoSerializer, ProyectoSerializer, UserSerializer
from .services import create_project_with_main_diagram


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer


class ProyectoViewSet(viewsets.ModelViewSet):
    serializer_class = ProyectoSerializer

    def get_queryset(self):
        user = self.request.user
        # Una membresía solo se crea al aceptar una invitación. Por tanto este
        # queryset nunca expone invitaciones pendientes ni proyectos ajenos.
        queryset = Proyecto.objects.filter(
            Q(creador=user) | Q(miembros__usuario=user)
        ).distinct()
        # La eliminación definitiva se realiza desde la papelera, por eso debe
        # poder resolver un proyecto archivado aun si no vino el query param.
        if self.action not in {'destroy', 'restaurar'}:
            archivados = self.request.query_params.get('archivados', '').lower() == 'true'
            queryset = queryset.filter(archivado=archivados)
        filtro = self.request.query_params.get('filtro')
        if filtro == 'mios':
            queryset = queryset.filter(creador=user)
        elif filtro == 'compartidos':
            queryset = queryset.exclude(creador=user)
        elif filtro == 'plantillas':
            queryset = queryset.filter(is_template=True)
        elif filtro not in (None, '', 'recientes'):
            raise ValidationError({'filtro': 'Usa recientes, mios, compartidos o plantillas.'})
        # El editor actual persiste React Flow en JSON (nodes/edges), mientras
        # datos históricos usan ClaseUML/RelacionUML. Subconsultas evitan los
        # productos cartesianos y el frontend recibe solo los totales.
        from diagramas.models import ClaseUML, Diagrama, RelacionUML

        json_nodes = Diagrama.objects.filter(proyecto_id=OuterRef('pk')).order_by().values(
            'proyecto_id'
        ).annotate(total=Coalesce(Sum(Func(
            'nodes', function='jsonb_array_length', output_field=IntegerField()
        )), Value(0))).values('total')[:1]
        json_edges = Diagrama.objects.filter(proyecto_id=OuterRef('pk')).order_by().values(
            'proyecto_id'
        ).annotate(total=Coalesce(Sum(Func(
            'edges', function='jsonb_array_length', output_field=IntegerField()
        )), Value(0))).values('total')[:1]
        clases = ClaseUML.objects.filter(diagrama__proyecto_id=OuterRef('pk')).order_by().values(
            'diagrama__proyecto_id'
        ).annotate(total=Count('pk')).values('total')[:1]
        relaciones = RelacionUML.objects.filter(diagrama__proyecto_id=OuterRef('pk')).order_by().values(
            'diagrama__proyecto_id'
        ).annotate(total=Count('pk')).values('total')[:1]
        return queryset.annotate(
            total_entidades=Greatest(
                Coalesce(Subquery(json_nodes), Value(0)), Coalesce(Subquery(clases), Value(0)),
            ),
            total_relaciones=Greatest(
                Coalesce(Subquery(json_edges), Value(0)), Coalesce(Subquery(relaciones), Value(0)),
            ),
        ).order_by('-updated_at', '-id')

    def perform_create(self, serializer):
        try:
            # El propietario procede exclusivamente de la sesión autenticada;
            # el serializer no acepta creador/propietario desde el body.
            serializer.instance = create_project_with_main_diagram(
                creator=self.request.user,
                nombre=serializer.validated_data['nombre'],
            )
        except IntegrityError:
            raise ValidationError({
                'nombre': 'Ya tienes un proyecto activo con este nombre.'
            })

    def perform_update(self, serializer):
        require_owner(self.request.user, serializer.instance)
        serializer.save()

    @transaction.atomic
    def perform_destroy(self, instance):
        require_owner(self.request.user, instance)
        # Todas las relaciones del proyecto usan CASCADE: miembros,
        # invitaciones y diagramas (con su contenido) se eliminan juntos.
        instance.delete()

    @action(detail=True, methods=['post'])
    def invitar(self, request, pk=None):
        proyecto = self.get_object()
        require_owner(request.user, proyecto)

        email = request.data.get('email')
        username = request.data.get('username')
        user_id = request.data.get('usuario_id')
        rol = request.data.get('rol', ProyectoMiembro.Rol.EDITOR)
        if not any((email, username, user_id)):
            raise ValidationError('Indica email, username o usuario_id para invitar.')
        if rol not in ProyectoMiembro.Rol.values or rol == ProyectoMiembro.Rol.PROPIETARIO:
            raise ValidationError({'rol': 'El rol debe ser arquitecto, editor o lector.'})

        usuarios = User.objects.all()
        if email:
            usuarios = usuarios.filter(email__iexact=email)
        elif username:
            usuarios = usuarios.filter(username__iexact=username)
        else:
            usuarios = usuarios.filter(pk=user_id)
        invitado = usuarios.first()
        if invitado is None:
            raise ValidationError({'usuario': 'No existe un usuario registrado con esos datos.'})
        if invitado.pk == proyecto.creador_id:
            raise ValidationError({'usuario': 'El propietario ya pertenece al proyecto.'})

        if proyecto.miembros.filter(usuario=invitado).exists():
            raise ValidationError({'usuario': 'Ese usuario ya pertenece al proyecto.'})
        InvitacionProyecto.objects.filter(
            proyecto=proyecto, invitado=invitado,
            estado=InvitacionProyecto.Estado.PENDIENTE,
        ).update(estado=InvitacionProyecto.Estado.RECHAZADA, respondida_en=timezone.now())
        invitacion = InvitacionProyecto.objects.create(
            proyecto=proyecto, invitador=request.user, invitado=invitado, rol=rol,
        )
        return Response(InvitacionProyectoSerializer(invitacion, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def archivar(self, request, pk=None):
        proyecto = self.get_object()
        require_owner(request.user, proyecto)
        proyecto.archivado = True
        proyecto.save(update_fields=['archivado', 'updated_at'])
        return Response(ProyectoSerializer(proyecto, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def restaurar(self, request, pk=None):
        """Restaura un proyecto desde la papelera al historial activo."""
        proyecto = self.get_object()
        require_owner(request.user, proyecto)
        if not proyecto.archivado:
            raise ValidationError({'detail': 'El proyecto ya está activo.'})
        proyecto.archivado = False
        try:
            proyecto.save(update_fields=['archivado', 'updated_at'])
        except IntegrityError:
            raise ValidationError({
                'nombre': 'Ya tienes un proyecto activo con este nombre. '
                          'Renómbralo antes de restaurarlo.'
            })
        return Response(ProyectoSerializer(proyecto, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def duplicar(self, request, pk=None):
        proyecto = self.get_object()
        duplicado = duplicate_project(proyecto, request.user, request.data.get('nombre'))
        return Response(ProyectoSerializer(duplicado, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['patch'], url_path=r'miembros/(?P<usuario_id>[^/.]+)')
    def cambiar_rol(self, request, pk=None, usuario_id=None):
        proyecto = self.get_object()
        require_owner(request.user, proyecto)
        rol = request.data.get('rol')
        if rol not in ProyectoMiembro.Rol.values or rol == ProyectoMiembro.Rol.PROPIETARIO:
            raise ValidationError({'rol': 'El rol debe ser arquitecto, editor o lector.'})
        try:
            miembro = proyecto.miembros.get(usuario_id=usuario_id)
        except ProyectoMiembro.DoesNotExist:
            raise ValidationError({'usuario': 'Ese usuario no es miembro del proyecto.'})
        if miembro.rol == ProyectoMiembro.Rol.PROPIETARIO:
            raise ValidationError({'usuario': 'No se puede cambiar el rol del propietario.'})
        miembro.rol = rol
        miembro.save(update_fields=['rol'])
        return Response(ProyectoSerializer(proyecto, context={'request': request}).data)

    @action(detail=True, methods=['delete'], url_path=r'colaboradores/(?P<usuario_id>[^/.]+)')
    def quitar_colaborador(self, request, pk=None, usuario_id=None):
        proyecto = self.get_object()
        require_owner(request.user, proyecto)
        deleted, _ = proyecto.miembros.exclude(
            rol=ProyectoMiembro.Rol.PROPIETARIO
        ).filter(usuario_id=usuario_id).delete()
        if not deleted:
            raise ValidationError({'usuario': 'Ese usuario no es miembro del proyecto.'})
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitacionProyectoViewSet(viewsets.GenericViewSet):
    serializer_class = InvitacionProyectoSerializer

    def get_queryset(self):
        # Reenviar/cancelar pertenecen al propietario que emitió la invitación;
        # aceptar/rechazar pertenecen exclusivamente al invitado.
        if self.action in {'reenviar', 'destroy'}:
            return InvitacionProyecto.objects.filter(
                proyecto__creador=self.request.user,
                estado=InvitacionProyecto.Estado.PENDIENTE,
            ).select_related('proyecto', 'invitado')
        return InvitacionProyecto.objects.filter(
            invitado=self.request.user,
            estado=InvitacionProyecto.Estado.PENDIENTE,
        ).select_related('proyecto', 'invitador')

    def list(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def aceptar(self, request, pk=None):
        invitacion = self.get_object()
        miembro, _ = ProyectoMiembro.objects.update_or_create(
            proyecto=invitacion.proyecto,
            usuario=request.user,
            defaults={'rol': invitacion.rol},
        )
        invitacion.estado = InvitacionProyecto.Estado.ACEPTADA
        invitacion.respondida_en = timezone.now()
        invitacion.save(update_fields=['estado', 'respondida_en'])
        miembro_data = {
            'id': miembro.id,
            'usuario': {
                'id': request.user.id,
                'username': request.user.username,
                'email': request.user.email,
            },
            'rol': miembro.rol,
        }
        transaction.on_commit(lambda: notify_invitation_accepted(
            proyecto_id=invitacion.proyecto_id,
            invitacion_id=invitacion.id,
            miembro=miembro_data,
        ))
        return Response(self.get_serializer(invitacion).data)

    @action(detail=True, methods=['post'])
    def rechazar(self, request, pk=None):
        invitacion = self.get_object()
        invitacion.estado = InvitacionProyecto.Estado.RECHAZADA
        invitacion.respondida_en = timezone.now()
        invitacion.save(update_fields=['estado', 'respondida_en'])
        return Response(self.get_serializer(invitacion).data)

    @action(detail=True, methods=['post'])
    def reenviar(self, request, pk=None):
        """Renueva una invitación pendiente del propietario.

        La aplicación no envía correo transaccional todavía; la fecha renovada
        permite al cliente volver a notificarla dentro de la aplicación.
        """
        invitacion = self.get_object()
        invitacion.fecha = timezone.now()
        invitacion.save(update_fields=['fecha'])
        return Response(self.get_serializer(invitacion).data)

    def destroy(self, request, *args, **kwargs):
        """Cancela una invitación pendiente emitida por el propietario."""
        invitacion = self.get_object()
        invitacion.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@transaction.atomic
def duplicate_project(origen, creator, nombre=None):
    """Copia el proyecto y todos sus diagramas UML sin compartir datos mutables."""
    from diagramas.models import AtributoUML, ClaseUML, Diagrama, RelacionUML

    copia = Proyecto.objects.create(
        nombre=nombre or f'{origen.nombre} (copia)', creador=creator,
        is_template=False,
    )
    for diagrama in origen.diagramas.all():
        nuevo_diagrama = Diagrama.objects.create(
            proyecto=copia, nombre=diagrama.nombre, nodes=diagrama.nodes, edges=diagrama.edges,
        )
        clases = {}
        for clase in diagrama.clases.all():
            nueva = ClaseUML.objects.create(
                diagrama=nuevo_diagrama, nombre=clase.nombre,
                posicion_x=clase.posicion_x, posicion_y=clase.posicion_y,
            )
            clases[clase.id] = nueva
            AtributoUML.objects.bulk_create([
                AtributoUML(clase=nueva, nombre=atributo.nombre,
                             tipo_dato=atributo.tipo_dato, visibilidad=atributo.visibilidad)
                for atributo in clase.atributos.all()
            ])
        RelacionUML.objects.bulk_create([
            RelacionUML(
                diagrama=nuevo_diagrama, clase_origen=clases[relacion.clase_origen_id],
                clase_destino=clases[relacion.clase_destino_id], tipo=relacion.tipo,
                multiplicidad_origen=relacion.multiplicidad_origen,
                multiplicidad_destino=relacion.multiplicidad_destino,
            )
            for relacion in diagrama.relaciones.all()
        ])
    return copia
