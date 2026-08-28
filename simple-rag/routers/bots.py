"""
Bot CRUD -- Phase 0/1 of README.md's multi-bot plan. A Bot is a
user-owned config bundle: name/description + custom instructions
(system_prompt), used by main.py's /chat route to build a bot-specific
system prompt (Phase 1) and, from Phase 2 onward, a bot-specific
knowledge base (see rag.py's bot_kb_name()). From Phase 3/4 onward, a
bot may also have its own MCP/Gmail/Calendar connections -- deleting a
bot cleans those up too (see delete_bot below) rather than leaving
orphaned per-bot OAuth rows behind.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config.database import get_db
from models import User, Bot, GmailConnection, CalendarConnection
from schemas import BotCreate, BotUpdate, BotOut
from security import get_current_user
import mcp_tools

router = APIRouter(prefix="/bots", tags=["bots"])


def _get_owned_bot(bot_id: str, db: Session, current_user: User) -> Bot:
    bot = (
        db.query(Bot)
        .filter(Bot.id == bot_id, Bot.user_id == current_user.id)
        .first()
    )
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return bot


@router.post("", response_model=BotOut)
def create_bot(
    payload: BotCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bot = Bot(
        user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
    )
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return bot


@router.get("", response_model=list[BotOut])
def list_bots(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Bot)
        .filter(Bot.user_id == current_user.id)
        .order_by(Bot.created_at.desc())
        .all()
    )


@router.get("/{bot_id}", response_model=BotOut)
def get_bot(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_bot(bot_id, db, current_user)


@router.patch("/{bot_id}", response_model=BotOut)
def update_bot(
    bot_id: str,
    payload: BotUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bot = _get_owned_bot(bot_id, db, current_user)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(bot, field, value)
    db.commit()
    db.refresh(bot)
    return bot


@router.delete("/{bot_id}", status_code=204)
async def delete_bot(
    bot_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bot = _get_owned_bot(bot_id, db, current_user)

    # Sessions that used this bot keep existing (and their message
    # history) -- they just fall back to the default assistant on their
    # next turn, same as a session that was never assigned a bot.
    # Explicit UPDATE rather than relying on DB-level ON DELETE
    # behavior, which isn't configured on this FK and would otherwise
    # block the delete (or vary by DB backend).
    from models import ChatSession

    db.query(ChatSession).filter(ChatSession.bot_id == bot.id).update({"bot_id": None})

    # README.md Phase 3/4: this bot's own MCP/Gmail/Calendar
    # connections are bot-specific and have no meaning once the bot is
    # gone -- delete them rather than leaving orphaned OAuth
    # tokens/selections behind. Does NOT touch the McpServer catalog
    # rows (those are user-scoped, shared across bots) or any other
    # bot's (or the user's bot-less) connections to those same
    # servers.
    await mcp_tools.delete_bot_connections(current_user.id, bot.id)
    db.query(GmailConnection).filter(
        GmailConnection.user_id == current_user.id, GmailConnection.bot_id == bot.id
    ).delete()
    db.query(CalendarConnection).filter(
        CalendarConnection.user_id == current_user.id, CalendarConnection.bot_id == bot.id
    ).delete()

    db.delete(bot)
    db.commit()
    return None
