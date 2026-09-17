from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Q
from django.utils import timezone

from .models import Diagrama


@database_sync_to_async
def user_can_access_diagram(user_id, diagram_id):
    return Diagrama.objects.filter(
        Q(proyecto__creador_id=user_id) | Q(proyecto__colaboradores__id=user_id),
        pk=diagram_id,
    ).exists()


@database_sync_to_async
def save_diagram_if_user_can_access(user_id, diagram_id, nodes, edges):
    """Guarda el lienzo solo si el usuario sigue perteneciendo al proyecto."""
    return Diagrama.objects.filter(
        Q(proyecto__creador_id=user_id) | Q(proyecto__colaboradores__id=user_id),
        pk=diagram_id,
    ).update(
        nodes=nodes,
        edges=edges,
        fecha_modificacion=timezone.now(),
    ) > 0


class DiagramaConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.diagrama_id = self.scope['url_route']['kwargs']['diagrama_id']
        user = self.scope['user']

        if not user.is_authenticated:
            await self.close(code=4401)
            return

        if not await user_can_access_diagram(user.id, self.diagrama_id):
            await self.close(code=4403)
            return

        # Todas las conexiones del mismo lienzo deben usar el mismo grupo.
        self.room_group_name = f'diagram_{self.diagrama_id}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name, self.channel_name
            )

    async def receive_json(self, content, **kwargs):
        """Persiste y distribuye una actualizacion completa de React Flow."""
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
        was_saved = await save_diagram_if_user_can_access(
            self.scope['user'].id, self.diagrama_id, nodes, edges
        )
        if not was_saved:
            await self.send_json({
                'type': 'diagram.error',
                'detail': 'No tienes permiso para editar este diagrama.',
            })
            await self.close(code=4403)
            return

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'diagram_update',
                'diagram_id': self.diagrama_id,
                'nodes': nodes,
                'edges': edges,
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
            })
