import logging

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
import json
from .settings import settings
from . import redis as redis_module

logger = logging.getLogger(__name__)

# The monitoring endpoints report on the dependencies a session needs, and are
# read most during an outage -- so they must not need a session themselves.
SESSIONLESS_PATHS = {"/health", "/metrics"}


def service_unavailable() -> JSONResponse:
    """The response for a request that could not reach Redis.

    A readable body the client can act on, and a hint of when to try again.
    """
    return JSONResponse(
        {"detail": "The service is temporarily unavailable. Wait a moment and try again."},
        status_code=503,
        headers={"Retry-After": "5"},
    )


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in SESSIONLESS_PATHS:
            return await call_next(request)
        cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
        try:
            token = request.cookies.get(settings.AUTH_COOKIE_NAME)
            account_email = None
            persistent = False
            if token and len(token) == 64 and all(ch in "0123456789abcdef" for ch in token):
                account_sid = await redis_module.redis.get(f"auth-token:{token}")
                if account_sid:
                    raw_account = await redis_module.redis.get(f"account:{account_sid}")
                    if raw_account:
                        try:
                            account = json.loads(raw_account)
                        except (json.JSONDecodeError, TypeError):
                            account = None
                        if isinstance(account, dict) and account.get("sid") == account_sid:
                            cookie = account_sid
                            account_email = account.get("email")
                            persistent = True
            # The compatibility sid cookie is not an account credential. If
            # it names an account namespace but its separate auth token is
            # absent, discard it instead of letting a logged-out browser fall
            # back to the account's private workspace.
            if not persistent and cookie and await redis_module.redis.exists(f"account:{cookie}"):
                cookie = None
            sid = await redis_module.ensure_session(cookie, persistent=persistent)
        except RedisError:
            # This middleware sits outside the app's exception handlers, so an
            # error here would otherwise become a bare 500.
            logger.warning("session store unavailable for %s %s", request.method, request.url.path)
            return service_unavailable()
        request.state.sid = sid
        request.state.account_email = account_email
        resp: Response = await call_next(request)
        if sid != cookie:
            resp.set_cookie(
                settings.SESSION_COOKIE_NAME, sid,
                max_age=settings.SESSION_TTL_SECONDS,
                httponly=True, secure=settings.COOKIE_SECURE,
                samesite=settings.COOKIE_SAMESITE, domain=settings.COOKIE_DOMAIN, path="/",
            )
        return resp
