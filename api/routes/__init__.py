from fastapi import APIRouter

from .pipelex import router as pipelex_router
from .storage import router as storage_router
from .uploader import router as uploader_router

# NOTE: the version router is NOT composed here — `GET /version` is always
# public (protocol handshake), so `api.main` mounts it under `/v1` directly,
# WITHOUT the auth dependency this composite router gets wrapped in.
router = APIRouter()

router.include_router(pipelex_router)
router.include_router(uploader_router)
router.include_router(storage_router)
