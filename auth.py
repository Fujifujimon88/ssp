"""JWT認証・パスワードハッシュ（bcrypt直接使用でpasslib互換問題を回避）"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer

from config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(publisher_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": publisher_id, "exp": expire},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload.get("sub")
    except JWTError:
        return None


async def verify_admin_key(key: str = Security(admin_key_header)) -> None:
    if not key or key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key",
            headers={"WWW-Authenticate": "X-Admin-Key"},
        )


async def get_current_publisher_id(token: str = Depends(oauth2_scheme)) -> str:
    publisher_id = decode_token(token)
    if not publisher_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return publisher_id


# ── ポータル認証（代理店・店舗） ─────────────────────────────────
from fastapi import Request

PORTAL_COOKIE_NAME = "portal_token"
_PORTAL_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7日間


def create_portal_token(entity_type: str, entity_id: str) -> str:
    """
    entity_type: "agency" | "dealer"
    entity_id:   str（AgencyDB.id は int だが str に変換して渡すこと）
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=_PORTAL_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": entity_id, "type": entity_type, "exp": expire},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def decode_portal_token(token: str) -> Optional[dict]:
    """Returns {"sub": id_str, "type": "agency"|"dealer"} or None."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        if "type" not in payload or "sub" not in payload:
            return None
        return {"sub": payload["sub"], "type": payload["type"]}
    except JWTError:
        return None


async def get_portal_session(request: Request) -> dict:
    """FastAPI Dependency: HTTPOnly Cookieを読みデコードしたペイロードを返す。未認証はログインへリダイレクト。"""
    token = request.cookies.get(PORTAL_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/portal/login"},
            detail="ログインが必要です",
        )
    payload = decode_portal_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/portal/login"},
            detail="セッションが無効です",
        )
    return payload
