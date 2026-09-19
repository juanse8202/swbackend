"""Notificaciones WebSocket disparadas tras confirmar cambios de proyectos."""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def notify_invitation_accepted(*, proyecto_id, invitacion_id, miembro):
    """Avisa únicamente a las conexiones autorizadas de ese proyecto."""
    channel_layer = get_channel_layer()
    event = {
        'type': 'invitation_accepted',
        'proyecto_id': proyecto_id,
        'invitacion_id': invitacion_id,
        'miembro': miembro,
    }
    async_to_sync(channel_layer.group_send)(f'project_{proyecto_id}', event)


def notify_member_removed(*, proyecto_id, usuario_id,
                          detail='El propietario te eliminó del proyecto.'):
    """Notifica una expulsión únicamente a las conexiones del proyecto."""
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(f'project_{proyecto_id}', {
        'type': 'member_removed',
        'proyecto_id': proyecto_id,
        'usuario_id': usuario_id,
        'detail': detail,
    })
