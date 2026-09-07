import json
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Q

from .models import Diagrama


@database_sync_to_async
def user_can_access_diagram(user_id, diagram_id):
    return Diagrama.objects.filter(
        Q(proyecto__creador_id=user_id) | Q(proyecto__colaboradores__id=user_id),
        pk=diagram_id,
    ).exists()

class DiagramaConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Obtenemos el ID del diagrama desde la URL
        self.diagrama_id = self.scope['url_route']['kwargs']['diagrama_id']

        user = self.scope['user']
        if not user.is_authenticated:
            await self.close(code=4401)
            return

        if not await user_can_access_diagram(user.id, self.diagrama_id):
            await self.close(code=4403)
            return

        self.room_group_name = f'diagrama_{self.diagrama_id}'

        # Unimos al usuario a la "sala" del diagrama
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Sacamos al usuario de la sala al desconectarse
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    # Recibe el mensaje desde React (Frontend)
    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')
        payload = data.get('payload')

        # Retransmite el mensaje a todos los demás en la sala
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'diagram_message',
                'action': action,
                'payload': payload,
                'sender_channel_name': self.channel_name
            }
        )

    # Envía el mensaje a los clientes conectados
    async def diagram_message(self, event):
        # Evitamos que el mensaje le rebote al mismo usuario que lo envió
        if self.channel_name != event.get('sender_channel_name'):
            await self.send(text_data=json.dumps({
                'action': event['action'],
                'payload': event['payload']
            }))
