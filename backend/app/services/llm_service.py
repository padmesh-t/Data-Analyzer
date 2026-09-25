import json
import re
from typing import Optional

import httpx

from app.config import settings


class LLMService:
    def __init__(self):
        self.model_name = settings.LLM_MODEL
        self.api_url = settings.VLLM_API_URL
        self.api_key = settings.VLLM_API_KEY or "not-needed"
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> Optional[httpx.Client]:
        if self._client is None:
            try:
                self._client = httpx.Client(
                    base_url=self.api_url,
                    timeout=httpx.Timeout(120.0, connect=10.0),
                )
            except Exception:
                self._client = None
        return self._client

    def _get_db_dialect(self, connection_type: str) -> str:
        dialect_map = {
            "postgresql": "PostgreSQL",
            "mysql": "MySQL",
            "mariadb": "MySQL",
            "sqlserver": "SQL Server",
            "mongodb": "MongoDB (NoSQL)",
            "oracle": "Oracle SQL",
        }
        return dialect_map.get(connection_type, "SQL")

    def _call_vllm(self, messages: list, temperature: float = 0.1, max_tokens: int = 1024) -> Optional[str]:
        client = self.client
        if client is None:
            return None
        try:
            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {"Content-Type": "application/json"}
            if self.api_key and self.api_key != "not-needed":
                headers["Authorization"] = f"Bearer {self.api_key}"

            resp = client.post("/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            return content
        except Exception:
            self._client = None
            return None

    def _build_schema_prompt(self, schema_context: str, dialect: str) -> str:
        dialect_rules = ""
        if dialect == "Oracle SQL":
            dialect_rules = """
- ORACLE SQL SYNTAX RULES:
  * NEVER use the 'AS' keyword for table aliases (write 'FROM table_name t', NOT 'FROM table_name AS t').
  * NEVER use LIMIT. Use 'FETCH FIRST n ROWS ONLY' to limit rows.
  * String concatenation uses || (e.g. col1 || ' ' || col2).
  * In GROUP BY queries, every column in the SELECT clause that is not an aggregate function (SUM, AVG, COUNT, etc.) MUST appear in the GROUP BY clause."""
        elif dialect == "SQL Server":
            dialect_rules = """
- SQL SERVER SYNTAX RULES:
  * Use 'SELECT TOP n' to limit rows instead of LIMIT.
  * String concatenation uses +."""
        elif dialect == "PostgreSQL":
            dialect_rules = """
- POSTGRESQL SYNTAX RULES:
  * String concatenation uses || or CONCAT().
  * Use LIMIT n to limit rows."""
        elif dialect == "MySQL":
            dialect_rules = """
- MYSQL SYNTAX RULES:
  * String concatenation uses CONCAT().
  * Use LIMIT n to limit rows."""

        return f"""You are an expert SQL engineer. Given a database schema and a natural language user question, generate a single valid {dialect} query that accurately answers the question.

DATABASE CONTEXT AND SCHEMA:
{schema_context}

CRITICAL RULES FOR SQL GENERATION:
1. STRICT COLUMN GROUNDING: Every column you reference in SELECT, JOIN, WHERE, GROUP BY, or ORDER BY MUST exist under that specific table in the schema above.
   - NEVER invent non-existent column names (e.g. do NOT write 'project_spending', 'total_employee_salary_cost', 'employee_count', 'average_salary').
   - Use the actual column names from the schema: e.g. in table PROJECTS the spending column is 'SPENT', budget is 'BUDGET'. In EMPLOYEES salary is 'SALARY'.
2. SINGLE TABLE COMPLETENESS: If all required metrics and columns exist in a single table (e.g. both SPENT and BUDGET are in table PROJECTS), query ONLY that table. Do NOT join other tables unnecessarily.
3. PRE-AGGREGATE FIRST, THEN JOIN (ROW MULTIPLICATION / FAN-OUT PREVENTION):
   - When a question involves multiple child tables that each have a one-to-many relationship to the same parent table (e.g. EMPLOYEES has many employees per department, and PROJECTS has many projects per department):
     NEVER join multiple child tables directly in a single FROM clause before aggregation! (e.g. 5 employees * 2 projects produces 10 rows, multiplying salaries and spending!).
     Instead, you MUST pre-aggregate each child table independently in a CTE (WITH clause) or subquery grouped by the parent key (department_id), and then join the aggregated totals to the parent table.
4. NO SELECT ALIASES IN HAVING OR WHERE (ORACLE SQL):
   - In Oracle SQL, column aliases defined in SELECT (e.g. `AS total_spent`) CANNOT be referenced in HAVING or WHERE clauses.
   - Instead, wrap the query in a CTE (WITH clause) or subquery, and filter in the outer WHERE clause:
     `WITH dept_totals AS (SELECT d.department_name, NVL(e.total_salary, 0) AS total_salary, NVL(p.total_spent, 0) AS total_spent FROM ...) SELECT * FROM dept_totals WHERE total_spent > total_salary;`
5. FILTERING BY ENTITY OR CATEGORY NAMES: When the user question mentions an entity name or category (e.g. 'Engineering', 'Q2', 'Completed'):
   - Look at the [Sample Values] in the schema to find which table and column holds that value (e.g. DEPARTMENTS.DEPARTMENT_NAME contains 'Engineering').
   - You MUST include a WHERE clause filtering on that column: e.g. `WHERE D.DEPARTMENT_NAME = 'Engineering'`.
   - If querying a related table (e.g. EMPLOYEES), JOIN the parent table on the foreign key relationship:
     `SELECT AVG(E.SALARY) FROM EMPLOYEES E JOIN DEPARTMENTS D ON E.DEPARTMENT_ID = D.DEPARTMENT_ID WHERE D.DEPARTMENT_NAME = 'Engineering';`
6. 'WHO', 'WHICH', AND RANKING QUESTIONS:
   - When asked 'Who ...' (e.g. 'Who has highest salary?', 'Who is the top sales rep?'): SELECT the person's identity (`FIRST_NAME || ' ' || LAST_NAME AS FULL_NAME`) together with the metric, and use `ORDER BY <metric> DESC FETCH FIRST 1 ROWS ONLY`.
   - When asked 'Which department...', 'Which project...', or 'Which quarter...': SELECT the entity name (`DEPARTMENT_NAME`, `PROJECT_NAME`, `QUARTER`, etc.) together with the metric, and `ORDER BY <metric> DESC FETCH FIRST 1 ROWS ONLY`.
7. INDEPENDENT TABLES & MULTI-METRIC CTEs: Independent tables that have no foreign key relationship to other tables (such as company-wide financial tables) must NOT be joined directly in a single FROM clause. Compute each metric in its own Common Table Expression (WITH clause) using FETCH FIRST 1 ROWS ONLY and combine the single-row CTEs using CROSS JOIN.
8. AGGREGATIONS & GROUP BY: All non-aggregated columns in SELECT must appear in GROUP BY.
9. SYNTAX & DIALECT:{dialect_rules}
10. ROW LIMIT: Include FETCH FIRST 20 ROWS ONLY for multi-row queries unless answering a top 1 ranking question.

INTENT & RELEVANCE RULES:
1. UNRELATED QUESTIONS: If the user question is completely unrelated to the available tables and columns in the schema (such as general knowledge, weather, movies, sports, recipes, or outside domains not in the schema), do NOT generate any SQL query. Output strictly:
   UNRELATED: The connected database does not contain information to answer this question.
2. AMBIGUOUS QUESTIONS: If the user asks a question with subjective or undefined criteria (e.g. 'Who is the best?', 'Which is greatest?') with no specified metric, do NOT guess. Output strictly:
   AMBIGUOUS: The question is ambiguous. Please clarify which metric you would like to evaluate (e.g., salary, revenue, budget, performance).
3. VALID DATABASE QUESTIONS: Generate a single valid {dialect} query that accurately answers the question. Output your query strictly inside a ```sql ... ``` code block. Return ONLY the ```sql ... ``` block without explanation or conversational text."""

    def _fixup_sql(self, raw: str, dialect: str = "") -> str:
        if not raw or not raw.strip():
            return ""
        raw = raw.strip()

        # 1. Search for markdown code block anywhere in the text
        match = re.search(r"```(?:sql|json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
        if match:
            sql = match.group(1).strip()
        else:
            # 2. Extract starting from the first SQL keyword
            sql_match = re.search(
                r"\b(WITH\s+[a-zA-Z0-9_]+\s+AS|SELECT\b|INSERT\s+INTO|UPDATE\b|DELETE\s+FROM|SHOW\b|DESCRIBE\b)[\s\S]*",
                raw,
                flags=re.IGNORECASE,
            )
            sql = sql_match.group(0).strip() if sql_match else ""

        if not sql:
            return ""

        # 3. Strip any conversational text after the ending semicolon
        if ";" in sql:
            parts = sql.split(";")
            for p in parts:
                if p.strip():
                    sql = p.strip()
                    break

        sql = re.sub(r"\n+", "\n", sql).strip()
        sql = sql.rstrip(";") + ";"

        if dialect == "Oracle SQL":
            # Sanitize Oracle table aliases: Oracle does not accept 'FROM table AS alias'
            sql = re.sub(r"\b(FROM|JOIN)\s+([a-zA-Z0-9_]+)\s+AS\s+([a-zA-Z0-9_]+)\b", r"\1 \2 \3", sql, flags=re.IGNORECASE)
            # Replace LIMIT with FETCH FIRST n ROWS ONLY
            sql = re.sub(r"\bLIMIT\s+(\d+)\s*;?$", r"FETCH FIRST \1 ROWS ONLY;", sql, flags=re.IGNORECASE)
            # Normalize FETCH FIRST 1 ROW ONLY to FETCH FIRST 1 ROWS ONLY
            sql = re.sub(r"\bFETCH\s+FIRST\s+(\d+)\s+ROW\s+ONLY", r"FETCH FIRST \1 ROWS ONLY", sql, flags=re.IGNORECASE)
        return sql

    def _estimate_tokens(self, nl: str, sql: str) -> int:
        return len(nl.split()) * 3 + len(sql.split()) * 2

    def generate_sql(self, natural_language: str, schema_context: str, connection_type: str) -> tuple[str, str, int]:
        dialect = self._get_db_dialect(connection_type)
        system_prompt = self._build_schema_prompt(schema_context, dialect)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": natural_language},
        ]

        raw = self._call_vllm(messages, temperature=0.1)
        if not raw or raw.strip().startswith("ERROR:"):
            return "", "The AI model was unable to generate a SQL query for this question.", 0

        raw_trimmed = raw.strip()
        if raw_trimmed.startswith("UNRELATED:"):
            msg = raw_trimmed[len("UNRELATED:"):].strip()
            return "UNRELATED", msg, self._estimate_tokens(natural_language, msg)
        if raw_trimmed.startswith("AMBIGUOUS:"):
            msg = raw_trimmed[len("AMBIGUOUS:"):].strip()
            return "AMBIGUOUS", msg, self._estimate_tokens(natural_language, msg)

        sql = self._fixup_sql(raw, dialect)
        tokens = self._estimate_tokens(natural_language, sql)

        # Ask the LLM to explain the generated SQL
        explanation_messages = [
            {"role": "system", "content": "You are a database assistant. Explain what the following SQL query does in 1-2 concise, clear sentences."},
            {"role": "user", "content": f"User question: {natural_language}\nGenerated SQL:\n{sql}"},
        ]
        explanation_raw = self._call_vllm(explanation_messages, temperature=0.1, max_tokens=256)
        explanation = explanation_raw.strip() if explanation_raw else f"Executes query for: {natural_language}"

        return sql, explanation, tokens

    def fix_sql(self, natural_language: str, sql: str, error: str, schema_context: str, connection_type: str) -> tuple[str, str, int]:
        dialect = self._get_db_dialect(connection_type)
        system_prompt = self._build_schema_prompt(schema_context, dialect)
        fix_prompt = (
            f"The following {dialect} query failed with a database error.\n\n"
            f"User Question: {natural_language}\n\n"
            f"Failed SQL:\n{sql}\n\n"
            f"Database Error:\n{error}\n\n"
            f"INSTRUCTIONS TO FIX DYNAMICALLY:\n"
            f"1. Understand what caused the error (e.g. ORA-00904 = column does not exist, ORA-00918 = ambiguous column, ORA-00937 = non-aggregated column in SELECT missing from GROUP BY).\n"
            f"2. Inspect the schema context above to find the exact table and column names.\n"
            f"3. Regenerate the corrected query using ONLY tables and columns verified in the schema.\n"
            f"4. Return ONLY the corrected raw SQL query without conversational text."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": fix_prompt},
        ]
        raw = self._call_vllm(messages, temperature=0.1)
        if not raw or raw.strip().startswith("ERROR:"):
            return sql, f"Query execution failed: {error}", 0

        fixed_sql = self._fixup_sql(raw, dialect)
        tokens = self._estimate_tokens(natural_language, fixed_sql)

        explanation_messages = [
            {"role": "system", "content": "Explain briefly in 1-2 sentences what correction was made to fix the query error."},
            {"role": "user", "content": f"Question: {natural_language}\nFixed SQL:\n{fixed_sql}\nPrevious Error:\n{error}"},
        ]
        explanation_raw = self._call_vllm(explanation_messages, temperature=0.1, max_tokens=256)
        explanation = explanation_raw.strip() if explanation_raw else f"Corrected SQL to resolve: {error}"

        return fixed_sql, explanation, tokens

    def explain_sql(self, sql: str, natural_language: str) -> str:
        messages = [
            {"role": "system", "content": "You are a database assistant. Explain what the SQL query does in 1-2 concise sentences."},
            {"role": "user", "content": f"Question: {natural_language}\nSQL:\n{sql}"},
        ]
        raw = self._call_vllm(messages, temperature=0.1, max_tokens=256)
        return raw.strip() if raw else "Executes the specified SQL query."

    def optimize_query(self, sql: str) -> list[dict]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a database performance expert. Analyze the SQL query and provide performance optimization suggestions. "
                    "Return ONLY a valid JSON array of objects with keys: 'type' (string), 'description' (string), 'impact' ('high'|'medium'|'low'). "
                    "Do NOT wrap with backticks or markdown."
                ),
            },
            {"role": "user", "content": f"Analyze and optimize this SQL query:\n{sql}"},
        ]
        raw = self._call_vllm(messages, temperature=0.1, max_tokens=512)
        if raw:
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw).strip()
            try:
                suggestions = json.loads(raw)
                if isinstance(suggestions, list):
                    return suggestions
            except (json.JSONDecodeError, TypeError):
                pass
        return [{
            "type": "general",
            "description": "Ensure appropriate indexes exist on filtered and joined columns.",
            "impact": "medium",
        }]

    def suggest_visualizations(self, columns: list[str], rows: list[list] = None, natural_language: str = "") -> list[dict]:
        """
        Dynamically suggest appropriate visualizations based purely on data shape,
        column types, and values (no hardcoded query rules).
        """
        if not columns or not rows:
            return [{"type": "table", "title": "Data Table", "config": {}}]

        row_count = len(rows)

        def is_col_numeric(idx: int) -> bool:
            num_count = 0
            sample = rows[:20]
            for r in sample:
                if idx < len(r) and r[idx] is not None:
                    try:
                        float(r[idx])
                        num_count += 1
                    except (ValueError, TypeError):
                        pass
            return num_count >= min(len(sample), 1)

        num_indices = [i for i in range(len(columns)) if is_col_numeric(i)]
        text_indices = [i for i in range(len(columns)) if i not in num_indices]

        # 1. Single scalar number (e.g. single aggregate metric)
        if row_count == 1 and len(columns) == 1 and len(num_indices) == 1:
            title_text = columns[0].replace("_", " ").title()
            return [
                {"type": "kpi", "title": title_text, "config": {"metric": columns[0]}},
                {"type": "table", "title": "Data Table", "config": {}},
            ]

        # 2. Single detail record with label dimension and metric
        if row_count == 1 and num_indices and text_indices:
            dim_col = columns[text_indices[0]]
            metric_col = columns[num_indices[0]]
            y_label = metric_col.replace("_", " ").title()
            x_label = dim_col.replace("_", " ").title()
            return [
                {"type": "bar_chart", "title": f"{y_label} by {x_label}", "config": {"x": dim_col, "y": metric_col}},
                {"type": "table", "title": "Data Table", "config": {}},
            ]

        # 3. Single record with no numeric metrics
        if row_count == 1:
            return [{"type": "table", "title": "Record Details", "config": {}}]

        # 4. No numeric metrics (pure tabular text)
        if not num_indices:
            return [{"type": "table", "title": "Data Table", "config": {}}]

        # 4. Multi-row tabular dataset with numerical metrics
        suggestions = []
        metric_col = columns[num_indices[0]]
        dim_col = columns[text_indices[0]] if text_indices else columns[0]

        is_time_series = any(k in dim_col.lower() for k in ("date", "time", "month", "quarter", "year", "day"))
        y_label = metric_col.replace("_", " ").title()
        x_label = dim_col.replace("_", " ").title()

        if is_time_series:
            suggestions.append({
                "type": "line_chart",
                "title": f"{y_label} Trend by {x_label}",
                "config": {"x": dim_col, "y": metric_col, "sort": "asc"},
            })
            suggestions.append({
                "type": "area_chart",
                "title": f"{y_label} Area by {x_label}",
                "config": {"x": dim_col, "y": metric_col},
            })
        else:
            suggestions.append({
                "type": "bar_chart",
                "title": f"{y_label} by {x_label}",
                "config": {"x": dim_col, "y": metric_col, "sort": "desc"},
            })
            if row_count <= 10:
                suggestions.append({
                    "type": "pie_chart",
                    "title": f"{y_label} Distribution by {x_label}",
                    "config": {"label": dim_col, "value": metric_col},
                })

        suggestions.append({"type": "table", "title": "Data Table", "config": {}})
        return suggestions

    def synthesize_data_summary(self, natural_language: str, sql: str, columns: list[str], rows: list[list]) -> str:
        """
        Synthesize rich data insights and visualization label assessment directly from the executed query.
        """
        if not rows or not columns:
            return f"Query executed successfully, but returned 0 rows for question: '{natural_language}'."

        sample_rows = rows[:10]
        data_preview = f"Columns: {', '.join(columns)}\nTotal Rows: {len(rows)}\nSample Rows:\n" + "\n".join(str(r) for r in sample_rows)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert AI Database Analyst with live database tool access. "
                    "Analyze the query results returned from the database to answer the user's question. "
                    "Provide your response adhering to this format:\n\n"
                    "### 🎯 Direct Answer\n"
                    "State the direct answer clearly and concisely using the live database values.\n\n"
                    "### 📊 Key Calculated Values\n"
                    "List the specific metrics and calculated figures using proper formatting (currency $, percentages %, totals, commas).\n\n"
                    "### 💡 Insights & Explanation\n"
                    "Provide a brief 1-2 sentence analytical explanation of the findings and data limitations if any.\n\n"
                    "### 🛠️ Execution Trace & Details\n"
                    "- **Intent**: The analytical goal.\n"
                    "- **Tables & Columns Used**: Tables and columns from the executed query.\n"
                    "- **Validation**: Validated against live database records.\n"
                    "- **Visualizable Labels Assessment**: State whether chartable labels are present and recommend the best visualization type."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User Request: {natural_language}\n\n"
                    f"Executed SQL Query:\n{sql}\n\n"
                    f"Database Results Data:\n{data_preview}\n\n"
                    "Please provide the complete analysis and label assessment:"
                ),
            },
        ]
        summary = self._call_vllm(messages, temperature=0.2, max_tokens=700)
        return summary.strip() if summary else f"Query executed successfully ({len(rows)} rows returned)."


llm_service = LLMService()
