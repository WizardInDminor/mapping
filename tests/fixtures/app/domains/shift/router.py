from fastapi import APIRouter
from app.domains.shift.shift_services import ShiftContextDTO, ShiftContextService
router = APIRouter(prefix='/shift', tags=['shift'])
_service = ShiftContextService()
@router.get('/current', response_model=ShiftContextDTO)
def get_current_shift():
    return _service.get_current_shift(conn=None)
