import os
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from config.database import get_db
from models import User

# ---- Config (put these in your .env) ----
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-in-.env")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_REFRESH_EXPIRE_MINUTES", "10080"))

# auto_error=False lets us return our own 401 with a clear message instead of
# FastAPI's generic "Not authenticated" when the header is missing entirely.
bearer_scheme = HTTPBearer(auto_error=False)


# ---- Password helpers ----

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# ---- JWT helpers ----

def _create_token(data: dict, expires_delta: timedelta | None, token_type: str) -> str:
    to_encode = data.copy()
    ttl_minutes = ACCESS_TOKEN_EXPIRE_MINUTES if token_type == "access" else REFRESH_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ttl_minutes))
    to_encode.update({"exp": expire, "type": token_type})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    return _create_token(data, expires_delta, "access")


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> str:
    return _create_token(data, expires_delta, "refresh")


def decode_access_token(token: str) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        if payload.get("type") not in (None, "access"):
            raise credentials_exception
        return payload
    except JWTError:
        raise credentials_exception


def decode_refresh_token(token: str) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None or payload.get("type") != "refresh":
            raise credentials_exception
        return payload
    except JWTError:
        raise credentials_exception


# ---- Dependency: get current logged-in user ----

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user