from fastapi import APIRouter
from app.applications.daily_operations.services.daily_operations_services import get_workcell_shift_activity as get_workcell_shift_activity_service
router = APIRouter(prefix='/daily-operations')
@router.get('/workcells/{workcell}/shift-activity', response_model=dict)
def get_workcell_shift_activity(workcell: str):
    return get_workcell_shift_activity_service(None, workcell)
