"""Procedure status rows and the actions currently allowed by each state."""
from datetime import date

from .cases import Absence, Case, Stage


def can_request_absence(case: Case) -> bool:
    return case.absence not in (Absence.REQUESTED, Absence.REQUESTING, Absence.NOT_REQUIRED)


def can_sign(case: Case, today: date) -> bool:
    return today > case.end_date and case.stage in (Stage.NO_AUTH, Stage.AUTH_RECEIVED, Stage.GENERATED)


def can_resign(case: Case) -> bool:
    """A finished procedure can sign its generated document again while it is still listed."""
    return case.completed_at is not None and case.stage == Stage.SIGNED


def steps(case: Case, today: date, *, busy: bool = False) -> list[dict]:
    rows = []
    if not case.no_auth:
        completed = case.stage != Stage.AWAITING_AUTH
        rows.append({'key': 'authorization', 'label': 'Autorización', 'action': None,
                     'state': 'completed' if completed else 'error' if case.last_problem else 'scheduled',
                     'detail': 'Recibida' if completed else 'Simulada' if case.simulated else 'Pendiente'})
    absence_details = {
        Absence.SCHEDULED: 'Programada', Absence.ASKING: 'Pendiente de tu respuesta',
        Absence.REQUESTING: 'En curso', Absence.REQUESTED: 'Solicitada',
        Absence.SKIPPED: 'No solicitada por tu elección', Absence.NOT_REQUESTED: 'No solicitada',
        Absence.UNCERTAIN: 'Envío sin confirmar; revisa USC', Absence.SIMULATED: 'Simulada; no enviada',
        Absence.NOT_REQUIRED: 'No necesaria: sin días laborables',
    }
    absence_state = ('completed' if case.absence in (Absence.REQUESTED, Absence.NOT_REQUIRED) else
                     'running' if case.absence == Absence.REQUESTING else
                     'scheduled' if case.absence in (Absence.SCHEDULED, Absence.ASKING) and not case.absence_problem else
                     'error')
    rows.append({'key': 'absence', 'label': 'Ausencia', 'state': absence_state,
                 'detail': absence_details[case.absence],
                 'action': 'absence' if can_request_absence(case) and not busy else None})
    signed = case.stage == Stage.SIGNED
    signing_error = case.last_problem and case.stage not in (Stage.AWAITING_AUTH, Stage.SIGNED)
    detail = ('Firmada y entregada' if case.document_delivered else 'Firmada; pendiente de entrega' if signed else
              'En curso' if busy else 'Error al generar o firmar' if signing_error else
              'Disponible después del último día' if today <= case.end_date else
              'Pendiente de autorización' if case.stage == Stage.AWAITING_AUTH else 'Pendiente')
    idle = not busy and case.absence != Absence.REQUESTING
    action = 'sign' if can_sign(case, today) else 'resign' if can_resign(case) else None
    rows.append({'key': 'sign', 'label': 'Firma', 'detail': detail,
                 'state': 'completed' if signed else 'running' if busy else 'error' if signing_error else 'scheduled',
                 'action': action if idle else None})
    return rows
