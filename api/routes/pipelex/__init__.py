from fastapi import APIRouter

from .agent import router as agent_router
from .build import router as build_router
from .pipeline import router as pipeline_router
from .validate import router as validate_router

router = APIRouter()

router.include_router(build_router)
router.include_router(pipeline_router)
router.include_router(validate_router)
router.include_router(agent_router)
