from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import transcriber

router = APIRouter(prefix="/stt", tags=["stt"])


@router.get("/status")
def stt_status():
    return transcriber.status()


@router.post("/prewarm", status_code=202)
def stt_prewarm():
    transcriber.prewarm(blocking=False)
    return JSONResponse(status_code=202, content=transcriber.status())
