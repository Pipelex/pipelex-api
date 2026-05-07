from fastapi import APIRouter

from api.routes.version import router as version_router

from .pipelex import router as pipelex_router
from .storage import router as storage_router
from .uploader import router as uploader_router

router = APIRouter()

router.include_router(version_router)
router.include_router(pipelex_router)
router.include_router(uploader_router)
router.include_router(storage_router)
