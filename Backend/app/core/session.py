import logging

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
import json
import uuid
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
            authenticated = False
            if token and len(token) == 64 and all(ch in "0123456789abcdef" for ch in token):
                mapped = await redis_module.redis.get(f"auth-token:{token}")
                if mapped:
                    try:
                        auth_session = json.loads(mapped)
                    except (json.JSONDecodeError, TypeError):
                        auth_session = None
                    if isinstance(auth_session, dict):
                        auth_sid = auth_session.get("sid")
                        account_email = auth_session.get("email")
                        if isinstance(auth_sid, str) and len(auth_sid) == 32:
                            cookie = auth_sid
                            authenticated = True
                    else:
                        # Compatibility with tokens created before sessions
                        # were changed from account-scoped to login-scoped.
                        raw_account = await redis_module.redis.get(f"account:{mapped}")
                        if raw_account:
                            old_account = json.loads(raw_account)
                            if isinstance(old_account, dict):
                                # Upgrade pre-change account-wide logins to a
                                # fresh namespace for this login token.
                                cookie = uuid.uuid4().hex
                                account_email = old_account.get("email")
                                authenticated = True
                                remaining = await redis_module.redis.ttl(f"auth-token:{token}")
                                await redis_module.redis.set(
                                    f"auth-token:{token}",
                                    json.dumps({"sid": cookie, "email": account_email}),
                                    ex=remaining if remaining > 0 else settings.AUTH_TTL_SECONDS,
                                )
            if authenticated:
                await redis_module.redis.set(
                    f"auth-session:{cookie}", "1", ex=settings.SESSION_TTL_SECONDS
                )
            # A sid cookie is only an identifier. Once its authenticated
            # session token is gone, never fall back to that former private
            # namespace as an anonymous session.
            if not authenticated and cookie and (
                await redis_module.redis.exists(f"auth-session:{cookie}")
                or await redis_module.redis.exists(f"account:{cookie}")
            ):
                cookie = None
            sid = await redis_module.ensure_session(cookie)
        except RedisError:
            # This middleware sits outside the app's exception handlers, so an
            # error here would otherwise become a bare 500.
            logger.warning("session store unavailable for %s %s", request.method, request.url.path)
            return service_unavailable()
        request.state.sid = sid
        request.state.account_email = account_email
        request.state.authenticated = authenticated
        resp: Response = await call_next(request)
        if sid != request.cookies.get(settings.SESSION_COOKIE_NAME):
            resp.set_cookie(
                settings.SESSION_COOKIE_NAME, sid,
                httponly=True, secure=settings.COOKIE_SECURE,
                samesite=settings.COOKIE_SAMESITE, domain=settings.COOKIE_DOMAIN, path="/",
            )
        return resp
