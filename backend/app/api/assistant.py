from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai.provider import AIUnavailable, LLMError
from app.api.deps import DB, CurrentPrincipal, Principal, get_owned, web_only
from app.models import AIConversation, AIMessage
from app.services import assistant as svc

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    tools_used: list[str]
    created_at: datetime


def _messages(rows: list[AIMessage]) -> list[MessageOut]:
    """User-facing transcript: user and assistant text; tool calls collapse into `tools_used`."""
    out: list[MessageOut] = []
    pending_tools: list[str] = []
    for m in rows:
        if m.role == "tool":
            continue
        if m.role == "assistant" and m.tool_calls and not m.content.strip():
            pending_tools += [t["name"] for t in m.tool_calls]
            continue
        tools = pending_tools + [t["name"] for t in (m.tool_calls or [])] if m.role == "assistant" else []
        if m.role == "assistant":
            pending_tools = []
        out.append(MessageOut(id=m.id, role=m.role, content=m.content, tools_used=tools, created_at=m.created_at))
    return out


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(db: DB, p: CurrentPrincipal) -> list[Any]:
    return list(
        db.scalars(
            select(AIConversation)
            .where(AIConversation.user_id == p.id)
            .order_by(AIConversation.updated_at.desc())
            .limit(50)
        )
    )


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(db: DB, p: Principal = Depends(web_only)) -> AIConversation:
    c = AIConversation(user_id=p.id, title="New conversation")
    db.add(c)
    db.commit()
    return c


@router.get("/conversations/{cid}/messages", response_model=list[MessageOut])
def get_messages(cid: str, db: DB, p: CurrentPrincipal) -> list[MessageOut]:
    get_owned(db, AIConversation, cid, p.id)
    rows = list(db.scalars(select(AIMessage).where(AIMessage.conversation_id == cid).order_by(AIMessage.created_at)))
    return _messages(rows)


class SendIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


@router.post("/conversations/{cid}/messages", response_model=list[MessageOut])
def send_message(cid: str, body: SendIn, db: DB, p: Principal = Depends(web_only)) -> list[MessageOut]:
    convo = get_owned(db, AIConversation, cid, p.id)
    try:
        created = svc.send(db, p.user, convo, body.content)
    except AIUnavailable as exc:
        raise HTTPException(503, "Connect an AI provider to use the assistant.") from exc
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    return _messages(created)


@router.delete("/conversations/{cid}", status_code=204)
def delete_conversation(cid: str, db: DB, p: Principal = Depends(web_only)) -> None:
    db.delete(get_owned(db, AIConversation, cid, p.id))
    db.commit()
