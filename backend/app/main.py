from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .auth import current_role
from .config import DEMO_DISCLAIMER, settings
from .db import engine
from .errors import DomainError
from .models import Base
from .routers import admin, analytics, cohorts, devices, finance, master, ops


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)  # dev convenience; production uses `alembic upgrade head`
    yield


app = FastAPI(
    dependencies=[Depends(current_role)],
    title="Revise Recovery OS API",
    version="0.1.0",
    description=DEMO_DISCLAIMER,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def domain_error(_: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message, "details": exc.details},
    )


for r in (
    master.router,
    cohorts.router,
    ops.router,
    finance.router,
    analytics.router,
    devices.router,
    admin.router,
):
    app.include_router(r)


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503, content={"status": "degraded", "database": f"error: {type(exc).__name__}"}
        )
    return {
        "status": "ok",
        "database": db_status,
        "dialect": engine.dialect.name,
        "disclaimer": DEMO_DISCLAIMER,
    }
