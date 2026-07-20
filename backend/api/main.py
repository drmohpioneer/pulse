import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router


DEFAULT_ALLOWED_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def create_app() -> FastAPI:
    """Create the FastAPI application shell."""
    app = FastAPI(title="Pulse API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins_from_env(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


def allowed_origins_from_env() -> list[str]:
    raw = os.getenv("PULSE_ALLOWED_ORIGINS")
    if raw is None:
        return list(DEFAULT_ALLOWED_ORIGINS)
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or list(DEFAULT_ALLOWED_ORIGINS)


app = create_app()
