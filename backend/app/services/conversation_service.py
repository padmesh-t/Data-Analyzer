from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models.conversation import Conversation, ConversationMessage
from app.schemas.conversation import (
    ConversationResponse,
    ConversationMessageResponse,
    CreateConversationRequest,
    UpdateConversationRequest,
    SendMessageRequest,
)
from app.services.llm_service import llm_service
from app.services.connection_service import get_connector
from app.services.query_service import _serialize_rows


def list_conversations(
    db: Session,
    user_id: int,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[ConversationResponse], int]:
    query = db.query(Conversation).filter(Conversation.user_id == user_id)
    total = query.count()
    offset = (page - 1) * per_page
    items = query.order_by(Conversation.updated_at.desc()).offset(offset).limit(per_page).all()
    return [ConversationResponse.model_validate(c) for c in items], total


def create_conversation(
    db: Session,
    data: CreateConversationRequest,
    user_id: int,
) -> ConversationResponse:
    title = data.title or "New Conversation"
    conv = Conversation(
        user_id=user_id,
        title=title,
        database_id=data.database_id,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationResponse.model_validate(conv)


def get_conversation(db: Session, conversation_id: int, user_id: int) -> ConversationResponse:
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationResponse.model_validate(conv)


def delete_conversation(db: Session, conversation_id: int, user_id: int) -> bool:
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return True


def update_conversation(
    db: Session,
    conversation_id: int,
    data: UpdateConversationRequest,
    user_id: int,
) -> ConversationResponse:
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if data.title is not None:
        conv.title = data.title
    if data.database_id is not None:
        conv.database_id = data.database_id
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conv)
    return ConversationResponse.model_validate(conv)


def get_messages(
    db: Session,
    conversation_id: int,
    user_id: int,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[ConversationMessageResponse], int]:
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    query = db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation_id,
    )
    total = query.count()
    offset = (page - 1) * per_page
    items = query.order_by(ConversationMessage.created_at.asc()).offset(offset).limit(per_page).all()
    return [ConversationMessageResponse.model_validate(m) for m in items], total


def send_message(
    db: Session,
    conversation_id: int,
    data: SendMessageRequest,
    user_id: int,
) -> ConversationMessageResponse:
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_msg = ConversationMessage(
        conversation_id=conversation_id,
        role="user",
        content=data.content,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    prev_messages = db.query(ConversationMessage).filter(
        ConversationMessage.conversation_id == conversation_id,
    ).order_by(ConversationMessage.created_at.asc()).limit(10).all()

    history = "\n".join(
        f"{'User' if m.role == 'user' else 'Assistant'}: {m.content[:200]}"
        for m in prev_messages[-5:]
    )

    schema_context = ""
    db_conn = None

    if conv.database_id:
        from app.models.connection import DatabaseConnection

        db_conn = db.query(DatabaseConnection).filter(DatabaseConnection.id == conv.database_id).first()
        if db_conn:
            try:
                connector = get_connector(db_conn)
                schema = connector.get_schema()
                lines = []
                for table in schema.tables:
                    cols = ", ".join(f"{c.name} ({c.data_type})" for c in table.columns[:25])
                    lines.append(f"Table: {table.name} [{cols}]")
                schema_context = "\n".join(lines[:30])
            except Exception:
                pass

    dialect = db_conn.connection_type if db_conn else "postgresql"
    sql, explanation, tokens_used = llm_service.generate_sql(
        data.content, schema_context, dialect,
    )

    generated_sql = sql
    results = None
    error_message = None

    if db_conn and sql and not sql.startswith("ERROR:"):
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                connector = get_connector(db_conn)
                import time
                start = time.time()
                raw_results = connector.execute_query(sql)
                elapsed = int((time.time() - start) * 1000)
                columns = raw_results.get("columns", [])
                rows = _serialize_rows(raw_results.get("rows", []))
                results = {
                    "columns": columns,
                    "rows": rows[:1000],
                    "row_count": len(rows),
                    "execution_time_ms": elapsed,
                }
                error_message = None
                break
            except Exception as e:
                error_message = str(e)
                if attempt < max_retries:
                    sql, explanation, retry_tokens = llm_service.fix_sql(
                        data.content, sql, error_message, schema_context, dialect,
                    )
                    generated_sql = sql
                    tokens_used = (tokens_used or 0) + (retry_tokens or 0)
                    if sql.startswith("ERROR:"):
                        break

    response_content = f"{explanation}\n\n```sql\n{sql}\n```"
    if results:
        response_content += f"\n\n*Returned {results['row_count']} rows in {results['execution_time_ms']}ms*"
    if error_message:
        response_content += f"\n\n*Error: {error_message}*"

    assistant_msg = ConversationMessage(
        conversation_id=conversation_id,
        role="assistant",
        content=response_content,
        tool_results=results if results else ({"error": error_message} if error_message else None),
        tokens_used=tokens_used,
        model_used=llm_service.model_name,
    )
    db.add(assistant_msg)
    conv.message_count = (conv.message_count or 0) + 2
    conv.total_tokens = (conv.total_tokens or 0) + (tokens_used or 0)
    conv.updated_at = datetime.now(timezone.utc)

    if conv.message_count <= 2:
        conv.title = data.content[:80]

    db.commit()
    db.refresh(assistant_msg)

    resp = ConversationMessageResponse.model_validate(assistant_msg)
    resp.generated_sql = generated_sql
    resp.results = results
    resp.error_message = error_message
    return resp
