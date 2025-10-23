from importlib.metadata import version

from fastapi import APIRouter

router = APIRouter()


@router.get("/pipelex_version")
async def pipelex_version() -> dict[str, str]:
    try:
        return {"version": version("pipelex")}
    except Exception as exc:
        msg = f"Error getting pipelex version: {exc}"
        raise ValueError(msg) from exc
