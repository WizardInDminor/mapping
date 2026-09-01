from app.applications.daily_operations.repos.shift_activity_repo import fetch_workcell_shift_activity
from app.domains.shift.shift_services import ShiftContextService

def get_workcell_shift_activity(conn, workcell: str):
    """Return normalized operational events for a Workcell and time window."""
    shift_service = ShiftContextService()
    shift = shift_service.get_current_shift(conn=conn)
    return fetch_workcell_shift_activity(conn, workcell, shift, shift)
