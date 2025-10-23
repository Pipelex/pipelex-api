from fastapi import APIRouter

from api.routes.main import router as main_router

from .pipelex import router as pipelex_router
from .uploader import router as uploader_router

router = APIRouter()

router.include_router(main_router)
router.include_router(pipelex_router)
router.include_router(uploader_router)
