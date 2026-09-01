from fastapi import FastAPI
from app.domains.shift.router import router as shift_router
from app.applications.daily_operations.routes.daily_operations_routes import router as daily_router
app = FastAPI()
app.include_router(shift_router, prefix='/api')
app.include_router(daily_router, prefix='/api')
