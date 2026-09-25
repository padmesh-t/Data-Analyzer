from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON, ForeignKey, func
from sqlalchemy.orm import relationship

from app.database import Base


class Query(Base):
    __tablename__ = "queries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    database_id = Column(Integer, ForeignKey("database_connections.id"), nullable=False)
    natural_language = Column(Text, nullable=False)
    generated_sql = Column(Text, nullable=True)
    explanation = Column(Text, nullable=True)
    status = Column(String(50), default="pending")
    result_columns = Column(JSON, nullable=True)
    result_rows = Column(JSON, nullable=True)
    row_count = Column(Integer, default=0)
    execution_time_ms = Column(Integer, nullable=True)
    tokens_used = Column(Integer, nullable=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)
    parent_query_id = Column(Integer, ForeignKey("queries.id"), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    user = relationship("User", backref="queries")
    database = relationship("DatabaseConnection", backref="queries")
    parent_query = relationship("Query", remote_side="Query.id", backref="follow_ups")
