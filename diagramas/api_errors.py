"""Stable, client-consumable errors for the authoritative diagram document."""

from rest_framework.exceptions import APIException


class DiagramApiError(APIException):
    """An error whose response can be rendered and focused by the canvas."""

    status_code = 400
    default_code = 'diagram_error'

    def __init__(
        self, message, *, code='diagram_error', status_code=None, target=None,
        details=None, current_revision=None,
    ):
        if status_code is not None:
            self.status_code = status_code
        payload = {
            'code': code,
            'message': message,
            'target': target,
            'current_revision': current_revision,
        }
        if details is not None:
            payload['details'] = details
        # APIException converts every scalar to ErrorDetail, which would turn
        # current_revision into a string. Keep machine-readable fields typed.
        super().__init__(detail=payload, code=code)
        self.detail = payload


def stale_revision_error(current_revision):
    return DiagramApiError(
        'El diagrama cambio mientras estabas editando. Actualiza el lienzo e intenta de nuevo.',
        code='stale_revision', status_code=409, current_revision=current_revision,
    )
