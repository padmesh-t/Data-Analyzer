import time
from typing import Optional

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from app.schemas.connection import ColumnInfo, TableSchema, SchemaResponse, DatabaseTestResult, SyncResult
from app.utils.error_messages import friendly_error


class PostgreSQLConnector:

    def __init__(self, host: str, port: int, database: str, user: str, password: str,
                 schema: str = "public", ssl: bool = False):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.schema = schema
        self.ssl = ssl
        self._connection = None

    def _get_conn_params(self):
        params = {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
        }
        if self.ssl:
            params["sslmode"] = "require"
        return params

    def connect(self):
        self._connection = psycopg2.connect(**self._get_conn_params())
        self._connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return self._connection

    def close(self):
        if self._connection and not self._connection.closed:
            self._connection.close()
            self._connection = None

    def test_connection(self) -> DatabaseTestResult:
        start = time.time()
        try:
            conn = psycopg2.connect(**self._get_conn_params())
            cur = conn.cursor()
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
            cur.close()
            conn.close()
            latency = int((time.time() - start) * 1000)
            return DatabaseTestResult(
                success=True,
                message="Connection successful",
                latency_ms=latency,
                server_version=version.split(",")[0].strip() if version else None,
            )
        except Exception as e:
            return DatabaseTestResult(
                success=False,
                message=friendly_error(str(e)),
            )

    def get_schema(self) -> SchemaResponse:
        conn = self.connect()
        try:
            cur = conn.cursor()
            tables = self._get_tables(cur, "BASE TABLE")
            views = self._get_tables(cur, "VIEW")
            cur.close()
            return SchemaResponse(
                database_id=0,
                schema_name=self.schema,
                tables=tables,
                views=views,
                last_synced_at=None,
            )
        finally:
            self.close()

    def sync_schema(self, database_id: int) -> SyncResult:
        start = time.time()
        errors = []
        tables_synced = 0
        columns_synced = 0

        try:
            schema = self.get_schema()
            tables_synced = len(schema.tables) + len(schema.views)
            for table in schema.tables:
                columns_synced += len(table.columns)
            for view in schema.views:
                columns_synced += len(view.columns)
        except Exception as e:
            errors.append(friendly_error(str(e)))

        duration = int((time.time() - start) * 1000)
        return SyncResult(
            database_id=database_id,
            status="completed" if not errors else "failed",
            tables_synced=tables_synced,
            columns_synced=columns_synced,
            duration_ms=duration,
            errors=errors,
            synced_at=__import__("datetime").datetime.utcnow(),
        )

    def _get_tables(self, cur, table_type: str) -> list[TableSchema]:
        cur.execute(
            sql.SQL("""
                SELECT
                    t.table_name,
                    t.table_type
                FROM information_schema.tables t
                WHERE t.table_schema = %s
                    AND t.table_type = %s
                ORDER BY t.table_name
            """),
            [self.schema, table_type],
        )
        tables = []
        for row in cur.fetchall():
            table_name = row[0]
            columns = self._get_columns(cur, table_name)
            tables.append(TableSchema(
                name=table_name,
                schema_name=self.schema,
                type="table" if table_type == "BASE TABLE" else "view",
                columns=columns,
            ))
        return tables

    def _get_columns(self, cur, table_name: str) -> list[ColumnInfo]:
        cur.execute(
            sql.SQL("""
                SELECT
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    c.column_default,
                    c.character_maximum_length,
                    CASE WHEN pk.column_name IS NOT NULL THEN TRUE ELSE FALSE END as is_pk
                FROM information_schema.columns c
                LEFT JOIN (
                    SELECT ku.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage ku
                        ON tc.constraint_name = ku.constraint_name
                        AND tc.table_schema = ku.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                        AND tc.table_schema = %s
                        AND tc.table_name = %s
                ) pk ON pk.column_name = c.column_name
                WHERE c.table_schema = %s
                    AND c.table_name = %s
                ORDER BY c.ordinal_position
            """),
            [self.schema, table_name, self.schema, table_name],
        )
        columns = []
        for row in cur.fetchall():
            columns.append(ColumnInfo(
                name=row[0],
                data_type=row[1],
                nullable=row[2] == "YES",
                is_primary_key=row[5],
                default_value=row[3],
                max_length=row[4],
            ))
        return columns

    def get_tables_list(self) -> list[dict]:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                sql.SQL("""
                    SELECT
                        table_name,
                        table_type,
                        (SELECT COUNT(*) FROM information_schema.columns
                         WHERE table_schema = %s AND table_name = t.table_name) as column_count
                    FROM information_schema.tables t
                    WHERE t.table_schema = %s
                    ORDER BY t.table_name
                """),
                [self.schema, self.schema],
            )
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "name": r[0],
                    "type": "table" if r[1] == "BASE TABLE" else "view",
                    "column_count": r[2],
                }
                for r in rows
            ]
        finally:
            self.close()

    def execute_query(self, sql: str) -> dict:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            if cur.description:
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                return {
                    "columns": columns,
                    "rows": [list(r) for r in rows],
                }
            return {"affected_rows": cur.rowcount}
        except Exception as e:
            raise type(e)(friendly_error(str(e)))
        finally:
            cur.close()
            self.close()

    def get_table_details(self, table_name: str) -> TableSchema:
        conn = self.connect()
        try:
            cur = conn.cursor()
            columns = self._get_columns(cur, table_name)
            cur.close()
            return TableSchema(
                name=table_name,
                schema_name=self.schema,
                columns=columns,
            )
        finally:
            self.close()
