from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pipelex.pipelex import Pipelex
from pipelex.system.runtime import IntegrationMode

from api.routes import router as api_router
from api.routes.pipelex.health import router as health_router
from api.security import get_auth_dependency

Pipelex.make(IntegrationMode.FASTAPI)

# Create FastAPI app with lifespan events
app = FastAPI(redirect_slashes=False)

# Add CORS middleware with specific configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(health_router)

# Register all other routes WITH authentication (auto-selects JWT or API key based on USE_JWT env var)
auth_dependency = get_auth_dependency()
app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(auth_dependency)])


@app.get("/")
async def root():
    return {"message": "Pipelex API"}
