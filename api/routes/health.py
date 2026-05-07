from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=dict)
async def get_health():
    return JSONResponse(
        content={
            "status": "ok",
            "message": "Pipelex API is running",
        }
    )
