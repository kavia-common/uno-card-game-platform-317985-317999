import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.uno_engine.store import InMemoryGameStore
from src.api.routes import build_games_router

openapi_tags = [
    {
        "name": "health",
        "description": "Health and diagnostics endpoints.",
    },
    {
        "name": "games",
        "description": "UNO game session management and actions.",
    },
]


app = FastAPI(
    title="UNO Backend API",
    description=(
        "In-memory UNO game engine with REST endpoints for session creation, state polling, "
        "and game actions (play/draw/pass/settings/restart)."
    ),
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# CORS: keep permissive for preview; allow explicit origins if provided.
frontend_url = os.getenv("REACT_APP_FRONTEND_URL")
allow_origins = ["*"] if not frontend_url else [frontend_url, "*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_store = InMemoryGameStore()
app.include_router(build_games_router(_store))


@app.get(
    "/",
    tags=["health"],
    summary="Health check",
    description="Lightweight health endpoint used by the frontend to verify connectivity.",
    operation_id="health_check",
)
# PUBLIC_INTERFACE
def health_check():
    """Health check endpoint.

    Returns:
        JSON object with a simple message.
    """
    return {"message": "Healthy"}
