from fastapi import APIRouter, Request, Body, Response
from ...core import redis as r
from ...core.settings import settings
from ...services.queue_service import add_item, set_progress

router = APIRouter()

@router.get("/session")
async def session_info(req: Request): return {"sid": req.state.sid}

@router.post("/session/end")
async def session_end(req: Request, response: Response):
    """End this browser/login session and erase its private workspace."""
    from ...services.session_lifecycle import end_session

    token = req.cookies.get(settings.AUTH_COOKIE_NAME)
    await end_session(req.state.sid)
    if token:
        await r.redis.delete(f"auth-token:{token}")
    response.delete_cookie(settings.AUTH_COOKIE_NAME, path="/", domain=settings.COOKIE_DOMAIN)
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/", domain=settings.COOKIE_DOMAIN)
    return {"ended": True}

@router.get("/queue")
async def queue_get(req: Request): return await r.get_queue(req.state.sid)

@router.post("/queue/add")
async def queue_add(req: Request, item: dict = Body(...)): return {"state": await add_item(req.state.sid, item)}

@router.patch("/queue/progress")
async def queue_progress(req: Request, update: dict = Body(...)): return {"state": await set_progress(req.state.sid, update)}

@router.delete("/queue")
async def queue_clear(req: Request):
    await r.put_queue(req.state.sid, {"items": [], "processing": None, "completed": []})
    return {"ok": True}
