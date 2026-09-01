class ShiftContextDTO: pass
class ShiftContextService:
    def get_current_shift(self, conn=None, as_of=None) -> ShiftContextDTO:
        return ShiftContextDTO()
