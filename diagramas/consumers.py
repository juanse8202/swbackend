from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Diagrama, PresenciaDiagrama
from .serializers import DiagramaSerializer
from proyectos.models import ProyectoMiembro


@database_sync_to_async
def project_for_authorized_diagram(user_id, diagram_id):
    return Diagrama.objects.filter(
        proyecto__miembros__usuario_id=user_id,
        pk=diagram_id,
    ).values_list('proyecto_id', flat=True).first()


@database_sync_to_async
def save_diagram_if_user_can_access(user_id, diagram_id, nodes, edges):
    """Validate and save a complete canvas under a row lock, with revision."""
    with transaction.atomic():
        diagram = Diagrama.objects.select_for_update().filter(pk=diagram_id).first()
        if diagram is None:
            return None
        try:
            membership = diagram.proyecto.miembros.get(usuario_id=user_id)
        except ProyectoMiembro.DoesNotExist:
            return None
        if membership.rol not in {
            ProyectoMiembro.Rol.PROPIETARIO,
            ProyectoMiembro.Rol.ARQUITECTO,
            ProyectoMiembro.Rol.EDITOR,
        }:
            return None
        if membership.rol == ProyectoMiembro.Rol.EDITOR and diagram.edges != edges:
            return None
        serializer = DiagramaSerializer(diagram, data={'nodes': nodes, 'edges': edges}, partial=True)
        if not serializer.is_valid():
            return None
        if diagram.nodes == nodes and diagram.edges == edges:
            return diagram.revision
        diagram.nodes = nodes
        diagram.edges = edges
        diagram.revision += 1
        diagram.save(update_fields=['nodes', 'edges', 'revision', 'fecha_modificacion'])
        return diagram.revision


@database_sync_to_async
def register_presence(diagram_id, user_id, channel_name):
    PresenciaDiagrama.objects.update_or_create(
        channel_name=channel_name,
        defaults={
            'diagrama_id': diagram_id,
            'usuario_id': user_id,
            'fecha_actividad': timezone.now(),
        },
    )


@database_sync_to_async
def remove_presence(channel_name):
    PresenciaDiagrama.objects.filter(channel_name=channel_name).delete()


@database_sync_to_async
def touch_presence(channel_name):
    return PresenciaDiagrama.objects.filter(channel_name=channel_name).update(
        fecha_actividad=timezone.now()
    ) > 0


@database_sync_to_async
def active_members(diagram_id):
    cutoff = timezone.now() - timedelta(seconds=45)
    PresenciaDiagrama.objects.filter(diagrama_id=diagram_id).filter(
        Q(fecha_actividad__isnull=True) | Q(fecha_actividad__lt=cutoff)
    ).delete()
    """Devuelve usuarios unicos, incluso si uno abrio varias pestañas."""
    members = []
    seen_user_ids = set()
    presences = PresenciaDiagrama.objects.filter(
        diagrama_id=diagram_id
    ).select_related('usuario').order_by('fecha_conexion')
    for presence in presences:
        if presence.usuario_id not in seen_user_ids:
            seen_user_ids.add(presence.usuario_id)
            members.append({
                'usuario': {
                    'id': presence.usuario_id,
                    'username': presence.usuario.username,
                    'email': presence.usuario.email,
                }
            })
    return members


class DiagramaConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.diagrama_id = self.scope['url_route']['kwargs']['diagrama_id']
        user = self.scope['user']

        if not user.is_authenticated:
            await self.close(code=4401)
            return

        self.proyecto_id = await project_for_authorized_diagram(user.id, self.diagrama_id)
        if self.proyecto_id is None:
            await self.close(code=4403)
            return

        # Todas las conexiones del mismo lienzo deben usar el mismo grupo.
        self.room_group_name = f'diagram_{self.diagrama_id}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        # Un solo socket del lienzo también recibe eventos del proyecto, como
        # la aceptación de invitaciones, sin abrir otra conexión en el cliente.
        self.project_group_name = f'project_{self.proyecto_id}'
        await self.channel_layer.group_add(self.project_group_name, self.channel_name)
        await self.accept()
        await register_presence(self.diagrama_id, user.id, self.channel_name)
        self.presence_active = True
        await self.broadcast_presence()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.leave_presence()
            await self.channel_layer.group_discard(
                self.room_group_name, self.channel_name
            )
            await self.channel_layer.group_discard(
                self.project_group_name, self.channel_name
            )

    async def leave_presence(self):
        """Retira esta pestaña de presencia; es seguro llamarlo varias veces."""
        if not getattr(self, 'presence_active', False):
            return
        await remove_presence(self.channel_name)
        self.presence_active = False
        await self.broadcast_presence()

    async def broadcast_presence(self):
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'presence_update',
                'miembros': await active_members(self.diagrama_id),
            },
        )

    async def receive_json(self, content, **kwargs):
        """Persiste y distribuye una actualizacion completa de React Flow."""
        if content.get('type') == 'presence.join':
            # El cliente anuncia su entrada al abrir o reconectar el socket.
            # connect() ya registra la presencia, pero tratar este evento como
            # idempotente evita falsos errores de sincronizacion y permite que
            # una pestaña que antes envio presence.leave vuelva a anunciarse.
            await register_presence(
                self.diagrama_id, self.scope['user'].id, self.channel_name
            )
            self.presence_active = True
            await self.broadcast_presence()
            return

        if content.get('type') == 'presence.leave':
            await self.leave_presence()
            return

        if content.get('type') == 'presence.heartbeat':
            if await touch_presence(self.channel_name):
                await self.broadcast_presence()
            return

        if content.get('type') != 'diagram.update':
            await self.send_json({
                'type': 'diagram.error',
                'detail': 'Tipo de evento no soportado.',
            })
            return

        # Una conexion de un lienzo no puede modificar otro usando un ID
        # manipulado desde el navegador.
        if str(content.get('diagram_id')) != str(self.diagrama_id):
            await self.send_json({
                'type': 'diagram.error',
                'detail': 'El diagrama del evento no coincide con la conexion.',
            })
            return

        nodes = content.get('nodes')
        edges = content.get('edges')
        if not isinstance(nodes, list) or not isinstance(edges, list):
            await self.send_json({
                'type': 'diagram.error',
                'detail': 'nodes y edges deben ser listas.',
            })
            return

        # El permiso se comprueba tambien en cada escritura, por si el
        # colaborador fue retirado despues de abrir el WebSocket.
        revision = await save_diagram_if_user_can_access(
            self.scope['user'].id, self.diagrama_id, nodes, edges
        )
        if revision is None:
            await self.send_json({
                'type': 'diagram.error',
                'detail': 'No tienes permiso para editar este diagrama.',
            })
            await self.close(code=4403)
            return

        await touch_presence(self.channel_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'diagram_update',
                'diagram_id': self.diagrama_id,
                'nodes': nodes,
                'edges': edges,
                'revision': revision,
                'sender_channel_name': self.channel_name,
            },
        )

    async def diagram_update(self, event):
        # El emisor ya tiene el cambio aplicado localmente. Reenviarlo solo a
        # los otros navegadores evita ciclos entre React Flow y WebSocket.
        if self.channel_name != event.get('sender_channel_name'):
            await self.send_json({
                'type': 'diagram.update',
                'diagram_id': event['diagram_id'],
                'nodes': event['nodes'],
                'edges': event['edges'],
                'revision': event.get('revision'),
            })

    async def presence_update(self, event):
        await self.send_json({
            'type': 'presence.update',
            'miembros': event['miembros'],
        })

    async def invitation_accepted(self, event):
        await self.send_json({
            'type': 'invitation.accepted',
            'proyecto_id': event['proyecto_id'],
            'invitacion_id': event['invitacion_id'],
            'miembro': event['miembro'],
        })

    async def member_removed(self, event):
        await self.send_json({
            'type': 'member.removed',
            'proyecto_id': event['proyecto_id'],
            'usuario_id': event['usuario_id'],
            'detail': event['detail'],
        })
