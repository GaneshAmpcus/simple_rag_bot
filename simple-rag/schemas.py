from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict


# ---- Auth ----

class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    created_at: datetime


class Token(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenData(BaseModel):
    user_id: str | None = None


# ---- Sessions / Conversations ----

class SessionCreate(BaseModel):
    title: str | None = "New Chat"
    bot_id: str | None = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    bot_id: str | None = None
    created_at: datetime


class MessageCreate(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    created_at: datetime



# --- Add these to your existing schemas.py, alongside the auth/session schemas ---

class QueryRequest(BaseModel):
    question: str
    top_k: int = 3
    kb_name: str | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None  # omit to start a new session
    message: str
    bot_id: str | None = None  # only used when starting a new session


class ChatResponse(BaseModel):
    session_id: str
    answer: str


# ---- Bots ----

class BotCreate(BaseModel):
    name: str
    description: str | None = None
    system_prompt: str | None = None


class BotUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None


class BotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str | None = None
    system_prompt: str | None = None
    created_at: datetime
    updated_at: datetime