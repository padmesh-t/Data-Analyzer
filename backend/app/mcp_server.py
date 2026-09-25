"""MCP (Model Context Protocol) server for Data-Taker.

Exposes database connections as MCP tools so AI agents can
discover, schema-inspect, and query databases via natural language or SQL.

Usage (SSE — mounted by main.py):
    GET  /mcp/  → Streamable HTTP endpoint

Usage (stdio — for Claude Desktop):
    python -m app.mcp_server
"""

from collections.abc import Callable
from typing import Optional

from mcp.server.mcpserver import MCPServer as FastMCP

from app.config import settings
from app.database import SessionLocal
from app.models.connection import DatabaseConnection
from app.schemas.query import QueryRequest
from app.services import query_service
from app.services.connection_service import (
    get_database,
    get_connector,
    get_schema as _get_schema,
    get_tables as _get_tables,
    get_table_details as _get_table_details,
    check_health as _check_health,
)

mcp = FastMCP("Data-Taker", instructions="""\
Data-Taker MCP server provides access to configured database connections.
Use `list_databases` first to discover available databases and their IDs.
Then use `get_schema`, `get_tables`, or `get_table_details` to explore structure.
Use `execute_sql` for raw SQL queries or `query_data` to ask questions in plain English.
""")


# ── Helpers ────────────────────────────────────────────────────────────────

def _db_session():
    """Yields a SQLAlchemy session that is closed after use."""
    db = SessionLocal()
    try:
        return db
    finally:
        db.close()


# ── Discovery Tools ─────────────────────────────────────────────────────────

@mcp.tool(description="List all active database connections available for querying.")
def list_databases() -> list[dict]:
    db = SessionLocal()
    try:
        connections = (
            db.query(DatabaseConnection)
            .filter(DatabaseConnection.is_active == True)
            .order_by(DatabaseConnection.name)
            .all()
        )
        return [
            {
                "id": c.id,
                "name": c.name,
                "type": c.connection_type,
                "host": c.host,
                "port": c.port,
                "database": c.database_name,
                "schema": c.schema_name or "",
            }
            for c in connections
        ]
    finally:
        db.close()


@mcp.tool(description="Get the full schema for a database: tables, columns, data types, and relationships.")
def get_schema(db_id: int) -> dict:
    db = SessionLocal()
    try:
        result = _get_schema(db, db_id)
        if result is None:
            return {"error": f"Database with id {db_id} not found"}
        return result.model_dump()
    finally:
        db.close()


@mcp.tool(description="List all tables in a database with estimated row counts.")
def get_tables(db_id: int) -> list[dict]:
    db = SessionLocal()
    try:
        result = _get_tables(db, db_id)
        if result is None:
            return [{"error": f"Database with id {db_id} not found"}]
        return result
    finally:
        db.close()


@mcp.tool(description="Get column details for a specific table: name, type, nullable, primary key, default value.")
def get_table_details(db_id: int, table_name: str) -> list[dict]:
    db = SessionLocal()
    try:
        result = _get_table_details(db, db_id, table_name)
        if result is None:
            return [{"error": f"Database with id {db_id} not found"}]
        return result
    finally:
        db.close()


# ── Query Tools ─────────────────────────────────────────────────────────────

@mcp.tool(description="Execute a raw SQL query against a database and return the results.")
def execute_sql(db_id: int, sql: str) -> dict:
    db = SessionLocal()
    try:
        db_conn = get_database(db, db_id)
        if not db_conn:
            return {"error": f"Database with id {db_id} not found"}
        connector = get_connector(db_conn)
        try:
            result = connector.execute_query(sql)
            return {
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
                "row_count": len(result.get("rows", [])),
            }
        finally:
            connector.close()
    finally:
        db.close()


@mcp.tool(description="Ask a natural language question about data in a database. AI converts it to SQL and returns results.")
def query_data(db_id: int, question: str) -> str:
    db = SessionLocal()
    try:
        req = QueryRequest(database_id=db_id, natural_language=question)
        result = query_service.execute_natural_language_query(db, req, user_id=0)
        if result.status == "failed":
            return f"Query failed: {result.error_message}"

        lines = [f"SQL: {result.generated_sql}"]
        if result.results:
            lines.append(f"Results ({result.results.row_count} rows):")
            lines.append(f"Columns: {', '.join(str(c) for c in result.results.columns)}")
            for row in result.results.rows[:20]:
                lines.append(f"  {row}")
            if result.results.row_count > 20:
                lines.append(f"  ... and {result.results.row_count - 20} more rows")
        return "\n".join(lines)
    finally:
        db.close()


# ── Health Tools ────────────────────────────────────────────────────────────

@mcp.tool(description="Check if a database connection is healthy and return ping latency in milliseconds.")
def check_health(db_id: int) -> dict:
    db = SessionLocal()
    try:
        result = _check_health(db, db_id)
        if result is None:
            return {"error": f"Database with id {db_id} not found"}
        return result.model_dump()
    finally:
        db.close()


# ── Entry points ────────────────────────────────────────────────────────────

# ── ASGI Auth Wrapper ─────────────────────────────────────────────────────

def _mcp_auth_wrapper(inner: Callable) -> Callable:
    """Wrap the MCP SSE app with API-key auth.

    Every HTTP request is validated against ``settings.MCP_API_KEY``.
    The key can be provided as:

    * ``X-API-Key`` HTTP header (recommended — automatically propagated
      to all requests by the MCP client SDK)
    * ``?api_key=<value>`` query parameter (convenient for ``curl`` tests)

    When ``MCP_API_KEY`` is empty (dev mode) auth is disabled.
    """
    from urllib.parse import parse_qs
    from starlette.responses import PlainTextResponse

    async def wrapper(scope: dict, receive: callable, send: callable) -> None:
        if scope["type"] == "http" and settings.MCP_API_KEY:
            # Try X-API-Key header first, then query param fallback
            raw_headers = {k: v for k, v in scope.get("headers", [])}
            api_key = raw_headers.get(b"x-api-key", b"").decode()
            if not api_key:
                qs = scope.get("query_string", b"").decode()
                params = {k: v[0] for k, v in parse_qs(qs).items()}
                api_key = params.get("api_key", "")

            if api_key != settings.MCP_API_KEY:
                return await PlainTextResponse(
                    "Unauthorized", status_code=401,
                )(scope, receive, send)
        await inner(scope, receive, send)

    return wrapper


def create_mcp_asgi() -> Callable:
    """Return the MCP SSE ASGI app (with optional auth) for mounting.

    Exposes:
      GET  /mcp/sse      — SSE transport endpoint
      POST /mcp/messages — message endpoint

    Auth:
      When ``MCP_API_KEY`` is set, every request must carry the key
      via ``X-API-Key`` header or ``?api_key=<value>`` query parameter.
    """
    return _mcp_auth_wrapper(mcp.sse_app())


if __name__ == "__main__":
    """Run MCP server in stdio mode (for Claude Desktop)."""
    mcp.run(transport="stdio")
