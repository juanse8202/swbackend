"""Post-commit WebSocket notifications for authoritative diagram writes."""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def publish_diagram_update(*, diagram_id, nodes, edges, revision, origin_request_id=None):
    """Publish only committed state; callers register this with on_commit()."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        f'diagram_{diagram_id}',
        {
            'type': 'diagram_update',
            'diagram_id': str(diagram_id),
            'nodes': nodes,
            'edges': edges,
            'revision': revision,
            'origin_request_id': origin_request_id,
            'sender_channel_name': None,
        },
    )
