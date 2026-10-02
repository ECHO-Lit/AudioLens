"""Email/password accounts whose private resources use a stable session namespace."""
import hashlib
import hmac
import json
import re
import secrets
import uuid

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import JSONResponse

from ...core import redis as redis_module
from ...core.settings import settings

router = APIRouter()
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PBKDF2_ROUNDS = 310_000


def _email_key(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        salt_hex, digest_hex = encoded.split("$", 1)
        candidate = _hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
        return hmac.compare_digest(candidate, digest_hex)
    except (ValueError, TypeError):
        return False


def _cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.AUTH_COOKIE_NAME, token,
        httponly=True, secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE, domain=settings.COOKIE_DOMAIN, path="/",
    )


async def _create_login(response: Response, account: dict) -> None:
    token = secrets.token_hex(32)
    # User credentials identify the person; the session namespace identifies
    # this login. A second browser/login therefore gets a clean, separate ECHO
    # workspace even when it belongs to the same account.
    sid = uuid.uuid4().hex
    await redis_module.redis.set(
        f"auth-token:{token}", json.dumps({"sid": sid, "email": account["email"]}),
        ex=settings.AUTH_TTL_SECONDS,
    )
    await redis_module.redis.set(
        f"auth-session:{sid}", "1", ex=settings.SESSION_TTL_SECONDS
    )
    _cookie(response, token)
    response.set_cookie(
        settings.SESSION_COOKIE_NAME, sid, httponly=True,
        secure=settings.COOKIE_SECURE, samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN, path="/",
    )


@router.post("/auth/register", status_code=201)
async def register(payload: dict = Body(...)):
    email = str(payload.get("email", "")).strip().lower()
    password = payload.get("password")
    if not _EMAIL.fullmatch(email) or len(email) > 254:
        return JSONResponse({"detail": "Enter a valid email address."}, status_code=422)
    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        return JSONResponse({"detail": "Password must be between 12 and 1024 characters."}, status_code=422)

    user_id = uuid.uuid4().hex
    account = {"email": email, "user_id": user_id, "password_hash": _hash_password(password)}
    user_key = f"account:{user_id}"
    email_key = f"account-email:{_email_key(email)}"
    created = await redis_module.redis.set(email_key, user_id, nx=True)
    if not created:
        return JSONResponse({"detail": "An account with that email already exists."}, status_code=409)
    await redis_module.redis.set(user_key, json.dumps(account))
    response = JSONResponse({"email": email, "authenticated": True}, status_code=201)
    await _create_login(response, account)
    return response


@router.post("/auth/login")
async def login(payload: dict = Body(...)):
    email = str(payload.get("email", "")).strip().lower()
    password = payload.get("password")
    user_id = await redis_module.redis.get(f"account-email:{_email_key(email)}") if _EMAIL.fullmatch(email) else None
    raw = await redis_module.redis.get(f"account:{user_id}") if user_id else None
    account = json.loads(raw) if raw else None
    if not account or not isinstance(password, str) or not _verify_password(password, account["password_hash"]):
        return JSONResponse({"detail": "Email or password is incorrect."}, status_code=401)
    response = JSONResponse({"email": account["email"], "authenticated": True})
    await _create_login(response, account)
    return response


@router.get("/auth/me")
async def me(request: Request):
    return {"authenticated": bool(getattr(request.state, "account_email", None)),
            "email": getattr(request.state, "account_email", None)}


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    from ...services.session_lifecycle import end_session

    await end_session(request.state.sid)
    if token:
        await redis_module.redis.delete(f"auth-token:{token}")
    await redis_module.redis.delete(f"auth-session:{request.state.sid}")
    response.delete_cookie(settings.AUTH_COOKIE_NAME, path="/", domain=settings.COOKIE_DOMAIN)
    # Rotate the legacy cookie so logout falls back to a fresh anonymous space.
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/", domain=settings.COOKIE_DOMAIN)
    return {"authenticated": False}
