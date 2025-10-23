from fastapi import APIRouter

from .pipe_builder import router as pipe_builder_router
from .pipeline import router as pipeline_router
from .plx_validator import router as plx_validator_router

router = APIRouter()

router.include_router(pipeline_router)
router.include_router(pipe_builder_router)
router.include_router(plx_validator_router)
