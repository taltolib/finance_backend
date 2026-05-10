from fastapi import FastAPI
from app.core.cors import setup_cors
from app.routes.health_routes import router as health_router
from app.routes.auth_routes import router as auth_router
from app.routes.humo_routes import router as humo_router
from app.routes.transaction_routes import router as transaction_router
from app.routes.dashboard_routes import router as dashboard_router
from app.routes.analytics_routes import router as analytics_router
from app.routes.kanban_routes import router as kanban_router

app = FastAPI(title="Finance App API")

setup_cors(app)

app.include_router(health_router, tags=["Health"])
app.include_router(auth_router, prefix="/auth", tags=["Auth"])
app.include_router(humo_router, tags=["HUMO"])
app.include_router(transaction_router, tags=["Transactions"])
app.include_router(dashboard_router, tags=["Dashboard"])
app.include_router(analytics_router, tags=["Analytics"])
app.include_router(kanban_router, prefix="/kanban", tags=["Kanban"])