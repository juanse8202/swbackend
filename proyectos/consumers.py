from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import ProyectoMiembro


@database_sync_to_async
def user_can_access_project(user_id, project_id):
    return ProyectoMiembro.objects.filter(
        proyecto_id=project_id, usuario_id=user_id
    ).exists()


class ProyectoConsumer(AsyncJsonWebsocketConsumer):
    """Canal de eventos del proyecto que no dependen de un diagrama concreto."""

    async def connect(self):
        self.proyecto_id = self.scope['url_route']['kwargs']['proyecto_id']
        user = self.scope['user']
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await user_can_access_project(user.id, self.proyecto_id):
            await self.close(code=4403)
            return
        self.room_group_name = f'project_{self.proyecto_id}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def invitation_accepted(self, event):
        await self.send_json({
            'type': 'invitation.accepted',
            'proyecto_id': event['proyecto_id'],
            'invitacion_id': event['invitacion_id'],
            'miembro': event['miembro'],
        })
