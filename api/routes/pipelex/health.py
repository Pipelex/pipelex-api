from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["pipeline"])


@router.get("/health", response_model=dict)
async def get_health():
    """Health check endpoint to verify the API is running.
    Returns a simple JSON response indicating the service is healthy.
    """
    return JSONResponse(
        content={
            "status": "ok",
            "message": "API is running",
        }
    )
