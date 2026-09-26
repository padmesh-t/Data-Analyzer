import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMProviderConfig:
    provider: str  # "openrouter", "grok", "local_proxy", "openai", "local", "mock"
    base_url: str
    api_key: str
    model: str
    headers: dict = field(default_factory=dict)
    timeout_seconds: float = 120.0


class LLMService:
    def __init__(self):
        self._clients: dict[str, httpx.Client] = {}

    @property
    def use_mock(self) -> bool:
        return getattr(settings, "LLM_USE_MOCK", False) or getattr(settings, "LLM_PROVIDER", "auto").lower() == "mock"

    @property
    def model_name(self) -> str:
        return self.resolve_provider().model

    @property
    def api_url(self) -> str:
        return self.resolve_provider().base_url

    @property
    def api_key(self) -> str:
        return self.resolve_provider().api_key or "not-needed"

    @property
    def client(self) -> Optional[httpx.Client]:
        config = self.resolve_provider()
        return self._get_client(config)

    def _get_local_provider_config(self) -> LLMProviderConfig:
        raw_url = getattr(settings, "VLLM_API_URL", "http://localhost:11434/v1") or "http://localhost:11434/v1"
        if "localhost:11434" in raw_url or "127.0.0.1:11434" in raw_url:
            import socket
            try:
                socket.gethostbyname("ollama")
                raw_url = raw_url.replace("localhost:11434", "ollama:11434").replace("127.0.0.1:11434", "ollama:11434")
            except Exception:
                pass
        return LLMProviderConfig(
            provider="local",
            base_url=raw_url.rstrip("/"),
            api_key=getattr(settings, "VLLM_API_KEY", "") or "",
            model=getattr(settings, "LLM_MODEL", "qwen2.5-coder:3b"),
        )

    def get_provider_chain(self) -> list[LLMProviderConfig]:
        """
        Build an ordered sequential chain of all available/configured LLM providers.
        
        Evaluation & Fallback Sequence:
        1. OpenRouter (if OPENROUTER_API_KEY is provided)
        2. GroqCloud (if GROQ_API_KEY is provided or key starts with 'gsk_')
        3. Grok / xAI (if GROK_API_KEY or XAI_API_KEY is provided)
        4. Local Proxy (if LOCAL_PROXY_URL is provided)
        5. OpenAI (if OPENAI_API_KEY is provided)
        6. Local LLM / Ollama (always present at the end of the chain as final fallback)
        """
        if self.use_mock:
            return [
                LLMProviderConfig(
                    provider="mock",
                    base_url="",
                    api_key="",
                    model="mock",
                )
            ]

        provider_override = getattr(settings, "LLM_PROVIDER", "auto").lower().strip()
        chain: list[LLMProviderConfig] = []
        seen_providers: set[str] = set()

        def add_config(cfg: Optional[LLMProviderConfig]):
            if cfg and cfg.provider not in seen_providers:
                chain.append(cfg)
                seen_providers.add(cfg.provider)

        # 1. OpenRouter
        openrouter_key = getattr(settings, "OPENROUTER_API_KEY", "").strip()
        if openrouter_key:
            headers = {
                "HTTP-Referer": getattr(settings, "OPENROUTER_SITE_URL", "http://localhost:3000"),
                "X-Title": getattr(settings, "OPENROUTER_APP_NAME", "Data-Analyzer"),
            }
            add_config(
                LLMProviderConfig(
                    provider="openrouter",
                    base_url=getattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
                    api_key=openrouter_key,
                    model=getattr(settings, "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct"),
                    headers=headers,
                )
            )

        # 2. GroqCloud / Grok detection
        groq_key = getattr(settings, "GROQ_API_KEY", "").strip()
        grok_key = getattr(settings, "GROK_API_KEY", "").strip() or getattr(settings, "XAI_API_KEY", "").strip()

        # If user put a GroqCloud key (gsk_...) into GROK_API_KEY or GROQ_API_KEY
        if groq_key or (grok_key and grok_key.startswith("gsk_")):
            effective_groq_key = groq_key or grok_key
            groq_model = getattr(settings, "GROQ_MODEL", "") or "openai/gpt-oss-120b"
            add_config(
                LLMProviderConfig(
                    provider="groq",
                    base_url=getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"),
                    api_key=effective_groq_key,
                    model=groq_model,
                )
            )
        elif grok_key:
            # Standard xAI Grok
            add_config(
                LLMProviderConfig(
                    provider="grok",
                    base_url=getattr(settings, "GROK_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
                    api_key=grok_key,
                    model=getattr(settings, "GROK_MODEL", "grok-2-latest"),
                )
            )

        # 3. Local Proxy / Custom OpenAI-compatible proxy (LiteLLM, LocalAI, LM Studio, etc.)
        proxy_url = getattr(settings, "LOCAL_PROXY_URL", "").strip()
        if proxy_url:
            raw_proxy = proxy_url
            if "localhost" in raw_proxy or "127.0.0.1" in raw_proxy:
                import socket
                try:
                    socket.gethostbyname("host.docker.internal")
                    raw_proxy = raw_proxy.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
                except Exception:
                    pass
            add_config(
                LLMProviderConfig(
                    provider="local_proxy",
                    base_url=raw_proxy.rstrip("/"),
                    api_key=getattr(settings, "LOCAL_PROXY_API_KEY", "").strip(),
                    model=getattr(settings, "LOCAL_PROXY_MODEL", "").strip() or getattr(settings, "LLM_MODEL", "qwen2.5-coder:3b"),
                )
            )

        # 4. OpenAI
        openai_key = getattr(settings, "OPENAI_API_KEY", "").strip()
        if openai_key:
            add_config(
                LLMProviderConfig(
                    provider="openai",
                    base_url=getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
                    api_key=openai_key,
                    model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
                )
            )

        # 5. Local LLM / Ollama (Always included at end of chain as final fallback)
        add_config(self._get_local_provider_config())

        # If an explicit provider override was requested, move it to the front of the chain
        if provider_override and provider_override not in ("auto", "mock"):
            explicit_config = next((c for c in chain if c.provider == provider_override), None)
            if explicit_config:
                chain.remove(explicit_config)
                chain.insert(0, explicit_config)

        return chain

    def resolve_provider(self) -> LLMProviderConfig:
        """Return the primary (first available) provider in the fallback chain."""
        chain = self.get_provider_chain()
        return chain[0] if chain else self._get_local_provider_config()

    def get_provider_info(self) -> dict:
        chain = self.get_provider_chain()
        primary = chain[0] if chain else self._get_local_provider_config()
        return {
            "active_provider": primary.provider,
            "model": primary.model,
            "base_url": primary.base_url,
            "has_api_key": bool(primary.api_key),
            "fallback_provider": "local",
            "fallback_chain": [f"{c.provider} ({c.model})" for c in chain],
            "total_providers_available": len(chain),
            "local_model": getattr(settings, "LLM_MODEL", "qwen2.5-coder:3b"),
            "local_url": getattr(settings, "VLLM_API_URL", "http://localhost:11434/v1"),
            "is_mock": self.use_mock,
        }

    def _get_client(self, config: LLMProviderConfig) -> Optional[httpx.Client]:
        if not config.base_url:
            return None
        cache_key = f"{config.provider}:{config.base_url}"
        if cache_key not in self._clients:
            try:
                self._clients[cache_key] = httpx.Client(
                    base_url=config.base_url,
                    timeout=httpx.Timeout(config.timeout_seconds, connect=10.0),
                )
            except Exception as e:
                logger.error(f"Failed to create httpx.Client for {config.provider}: {e}")
                return None
        return self._clients[cache_key]

    def _execute_chat_completion(
        self,
        config: LLMProviderConfig,
        messages: list,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> Optional[str]:
        client = self._get_client(config)
        if client is None:
            return None
        try:
            payload = {
                "model": config.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {"Content-Type": "application/json"}
            if config.api_key and config.api_key != "not-needed":
                headers["Authorization"] = f"Bearer {config.api_key}"
            if config.headers:
                headers.update(config.headers)

            resp = client.post("/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"LLM provider '{config.provider}' ({config.model}) execution failed: {e}")
            cache_key = f"{config.provider}:{config.base_url}"
            self._clients.pop(cache_key, None)
            return None

    def _call_llm(self, messages: list, temperature: float = 0.1, max_tokens: int = 1024) -> Optional[str]:
        """
        Execute LLM completion across the sequential provider fallback chain:
        Tries each configured provider sequentially. If a provider fails
        (network error, rate-limit, auth failure), logs a warning and tries
        the next provider in the chain until reaching the local LLM.
        """
        if self.use_mock:
            return None

        chain = self.get_provider_chain()
        attempted_providers = []

        for idx, config in enumerate(chain):
            attempted_providers.append(f"{config.provider} ({config.model})")
            logger.info(f"Attempting LLM call using provider: '{config.provider}' (model: '{config.model}')")
            content = self._execute_chat_completion(config, messages, temperature, max_tokens)
            if content is not None:
                if idx > 0:
                    logger.info(f"Successfully recovered using fallback provider '{config.provider}' after earlier failures.")
                return content

            # If not the last provider in the chain, log fallback attempt
            if idx < len(chain) - 1:
                next_provider = chain[idx + 1].provider
                logger.warning(
                    f"LLM provider '{config.provider}' ({config.model}) failed. "
                    f"Falling back to next provider in chain: '{next_provider}'..."
                )

        logger.error(f"All LLM providers in fallback chain failed: {', '.join(attempted_providers)}")
        return None

    # Backward compatibility alias
    def _call_vllm(self, messages: list, temperature: float = 0.1, max_tokens: int = 1024) -> Optional[str]:
        return self._call_llm(messages, temperature, max_tokens)

    def _get_db_dialect(self, connection_type: str) -> str:
        dialect_map = {
            "postgresql": "PostgreSQL",
            "mysql": "MySQL",
            "mssql": "T-SQL",
            "oracle": "Oracle SQL",
            "mongodb": "MongoDB Query",
            "sqlite": "SQLite",
        }
        return dialect_map.get(connection_type, "SQL")

    def _table_names_from_schema(self, schema_context: str) -> list[str]:
        lines = [line.strip() for line in schema_context.split("\n") if line.strip()]
        tables = []
        for line in lines:
            m = re.match(r"Table:\s*(\S+)", line)
            if m:
                tables.append(m.group(1))
        return tables

    def _has_table_in_schema(self, name: str, schema_context: str) -> bool:
        tables = self._table_names_from_schema(schema_context)
        return any(t.lower() == name.lower() for t in tables)

    def _build_schema_prompt(self, schema_context: str, dialect: str) -> str:
        dialect_rules = ""
        if dialect == "Oracle SQL":
            dialect_rules = """
- ORACLE SQL SYNTAX RULES:
  * NEVER use the 'AS' keyword for table aliases (write 'FROM table_name t', NOT 'FROM table_name AS t').
  * NEVER use LIMIT. Use 'FETCH FIRST n ROWS ONLY' (or 'FETCH FIRST n ROWS WITH TIES' when ranking entities with possible ties) to limit rows.
  * In Oracle SQL, 'ORDER BY col DESC' puts NULL values FIRST by default. ALWAYS add 'NULLS LAST' when ordering descending (e.g. 'ORDER BY metric DESC NULLS LAST') so NULL rows are never ranked first.
  * String concatenation uses || (e.g. col1 || ' ' || col2).
  * In GROUP BY queries, every column in the SELECT clause that is not an aggregate function (SUM, AVG, COUNT, etc.) MUST appear in the GROUP BY clause.
  * For table / schema exploration queries, use: 'SELECT table_name FROM user_tables;'."""
        elif dialect in ("SQL Server", "T-SQL"):
            dialect_rules = """
- SQL SERVER SYNTAX RULES:
  * Use 'SELECT TOP n' to limit rows instead of LIMIT.
  * String concatenation uses +.
  * For table / schema exploration queries, use: 'SELECT table_name FROM information_schema.tables WHERE table_type = \\'BASE TABLE\\';'."""
        elif dialect == "PostgreSQL":
            dialect_rules = """
- POSTGRESQL SYNTAX RULES:
  * String concatenation uses || or CONCAT().
  * Use LIMIT n to limit rows.
  * For table / schema exploration queries, use: 'SELECT table_name FROM information_schema.tables WHERE table_schema = \\'public\\' AND table_type = \\'BASE TABLE\\';'."""
        elif dialect == "MySQL":
            dialect_rules = """
- MYSQL SYNTAX RULES:
  * String concatenation uses CONCAT().
  * Use LIMIT n to limit rows.
  * For table / schema exploration queries, use: 'SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE();' (or 'SHOW TABLES;'). NEVER use 'table_schema = \\'public\\'')."""
        elif dialect == "SQLite":
            dialect_rules = """
- SQLITE SYNTAX RULES:
  * String concatenation uses ||.
  * Use LIMIT n to limit rows.
  * For table / schema exploration queries, use: 'SELECT name FROM sqlite_master WHERE type = \\'table\\' AND name NOT LIKE \\'sqlite_%\\';'."""

        return f"""You are an expert SQL engineer. Given a database schema and a natural language user question, generate a single valid {dialect} query that accurately answers the user's EXACT question.

DATABASE CONTEXT AND SCHEMA:
{schema_context}

## SQL GENERATION PRINCIPLES:

1. ONLY USE VERIFIED REAL SCHEMA TABLES & COLUMNS:
   - Use ONLY tables and columns that exist in the schema context above.
   - Do NOT invent or hallucinate non-existent table or column names.
   - For database schema / table listing questions (e.g. 'what tables exist', 'show tables in db'), use the dialect-specific system catalog query as specified below.

2. GROUP AGGREGATIONS ('IN EACH', 'PER', 'FOR EVERY', 'BY') VS GLOBAL EXTREMUM:
   - MULTI-GROUP BREAKDOWN:
     * When a question requests a metric across grouped dimensions (using semantic indicators like 'of each <dimension>', 'in each <dimension>', 'for each <dimension>', 'per <dimension>', 'by <dimension>', 'for every <dimension>', 'breakdown by <dimension>', 'all <dimension>s', e.g. 'highest salary in each department', 'highest salary of each department', 'total sales per region', 'average order value by customer', 'total budget of all projects'):
       - The query MUST use `GROUP BY <dimension>` and return records for ALL distinct groups.
       - NEVER apply `LIMIT 1`, `TOP 1`, or `FETCH FIRST 1 ROW ONLY` to group breakdown questions.
       - NEVER apply `FETCH FIRST 1 ROWS WITH TIES` to group breakdown questions.
     * When individual sub-entity records or names are requested per group (e.g. 'which employee has the highest salary in each department'):
       - Use standard window functions: `ROW_NUMBER() OVER (PARTITION BY <dimension> ORDER BY <metric> DESC)` in a CTE or subquery and filter `WHERE rn = 1`.
   - GLOBAL EXTREMUM / SINGLE WINNER:
     * Apply `ORDER BY <metric> DESC LIMIT 1` (or dialect equivalent) ONLY when asking for the single overall winner/loser across the entire dataset without group partitioning (e.g. 'Which department has the highest total salary overall?', 'Who is the highest paid employee in the company?').

3. MINIMAL JOINS & SINGLE-TABLE PRIORITY (AVOID UNNECESSARY JOINS):
   - If a single table already directly contains BOTH the requested grouping dimension/attribute (e.g. `dept`, `category`, `region`, `country`, `status`) AND the metric/target value (e.g. `salary`, `amount`, `revenue`, `price`, `rating`), **query that table directly without joining another table**.
   - NEVER introduce speculative or unverified string-equality JOINs to other tables (such as joining a separate dimension table where strings might be abbreviated or formatted differently) when the source table already holds the grouping column.
   - Join tables ONLY when the requested columns are split across tables and cannot be satisfied by a single table, following verified foreign keys.

4. ENTITY IDENTIFICATION (MANDATORY):
   - Whenever asking 'Which <entity>' (e.g. employee, project, client, vendor, vehicle, product), ALWAYS include the entity's primary identifying name/title column from the queried table in the SELECT clause so the entity is explicitly named.

5. ACCURATE METRIC MAPPING & DISAMBIGUATION:
   - Strictly distinguish between 'spending/spent' (actual expenditure) and 'budget' (allocated limit):
     * 'Spending' / 'Spent' / 'Cost' = actual expenditure (e.g. PROJECTS.SPENT, SUM(PROJECTS.SPENT), EMPLOYEES.SALARY, OPERATING_EXPENSES).
     * 'Budget' = allocated ceiling limit (e.g. PROJECTS.BUDGET, SUM(PROJECTS.BUDGET), DEPARTMENTS.BUDGET).
     * 'Remaining Budget' / 'Unspent' = (BUDGET - SPENT) or (SUM(BUDGET) - SUM(SPENT)).
     * When ranking by 'highest spending', ORDER BY spending (e.g. SUM(PROJECTS.SPENT) DESC), NOT by budget.
   - Use COALESCE / NVL / NULLIF appropriately to prevent division by zero (e.g. `NULLIF(denominator, 0)`) and handle NULL values cleanly.
   - Use `COUNT(DISTINCT column)` when counting unique entities, customers, or items.

6. PREVENT ROW MULTIPLICATION (FAN-OUT PREVENTION):
   - When calculating aggregated metrics from multiple one-to-many child tables related to the same parent table (e.g. employee salaries and project spending per department), NEVER join multiple child tables directly before aggregation in a single query.
   - Pre-aggregate each child table independently in a Common Table Expression (WITH clause) grouped by the foreign key first, and then join the pre-aggregated CTEs using LEFT JOIN.

7. TIME PERIOD & TEMPORAL INTEGRITY (MANDATORY RULE):
   - When the database contains multiple years with the same quarter names (or month names, e.g. 2024 Q1, 2024 Q2, 2025 Q1, 2025 Q2):
     * NEVER group by or rank by QUARTER alone (e.g. NEVER 'GROUP BY quarter').
     * '2024 Q2' and '2025 Q2' are distinct chronological periods and MUST NOT be combined.
     * If year and quarter are stored in separate columns (e.g. fiscal_year and quarter, year and quarter), ALWAYS group by, select, and rank by BOTH: `GROUP BY fiscal_year, quarter` (or `GROUP BY year, quarter`).
     * If a single period column exists (e.g. period = '2024 Q1'), group by that complete period column.
     * When asked ranking questions like 'Which quarter had the highest revenue?' or 'Top quarter':
       - Identify the complete quarter-period (Year + Quarter) for each record.
       - Rank complete individual periods (e.g. 2025 Q2), NOT combined quarter names across different years.
       - Return revenue, expenses, net profit, and profit margin belonging to that SAME exact period record.
     * NEVER SUM expenses, revenue, net profit, or metrics across different years merely because they share a quarter name.

8. DIALECT COMPLIANCE:
{dialect_rules}

9. OUTPUT FORMAT:
   - Provide ONLY the single executable {dialect} query enclosed strictly inside a ```sql ... ``` block."""

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
            sql = re.sub(r"\bDESC\b(?!\s+NULLS)", "DESC NULLS LAST", sql, flags=re.IGNORECASE)
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
            f"The following {dialect} query failed preflight structural validation or database execution.\n\n"
            f"User Question: {natural_language}\n\n"
            f"Previous SQL:\n{sql}\n\n"
            f"Validation/Database Error Diagnostic:\n{error}\n\n"
            f"INSTRUCTIONS TO DYNAMICALLY REGENERATE:\n"
            f"1. DO NOT guess column names or perform string replacements.\n"
            f"2. Inspect the schema context above to find the exact verified table and column names.\n"
            f"3. If multiple one-to-many child tables are joined, pre-aggregate each child table in a separate CTE (WITH clause) grouped by the foreign key BEFORE joining.\n"
            f"4. If analyzing, grouping, or ranking quarters/months across years, include BOTH year and quarter (e.g. 'GROUP BY fiscal_year, quarter') so quarters from different years (e.g. 2024 Q2 and 2025 Q2) are never combined.\n"
            f"5. If the question requests metrics 'in each', 'per', 'by', or 'for every' dimension, use GROUP BY across that dimension, return all groups, and NEVER apply a single-row LIMIT 1.\n"
            f"6. Regenerate the COMPLETE corrected raw SQL query using ONLY verified tables and columns.\n"
            f"7. Return ONLY the corrected raw SQL query strictly inside a ```sql ... ``` block."
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

        nl_lower = natural_language.lower()
        wants_line = any(w in nl_lower for w in ["line chart", "line graph", "line plot", "as a line", "line-chart", "trend line", "over time", "trend", "timeline", "monthly", "yearly", "daily", "growth", "history"])
        wants_area = any(w in nl_lower for w in ["area chart", "area graph", "area plot", "as an area", "area-chart"])
        wants_pie = any(w in nl_lower for w in [
            "pie chart", "pie graph", "pie plot", "as a pie", "pie-chart", "donut chart", "donut",
            "distribution", "share", "proportion", "percentage", "percent", "breakdown", "split", "ratio", "portion"
        ])
        wants_bar = any(w in nl_lower for w in ["bar chart", "bar graph", "bar plot", "as a bar", "bar-chart", "column chart", "histogram", "comparison", "ranking", "rank", "top", "highest", "lowest"])

        if wants_line:
            suggestions.append({
                "type": "line_chart",
                "title": f"{y_label} Trend by {x_label}" if is_time_series else f"{y_label} by {x_label}",
                "config": {"x": dim_col, "y": metric_col, "sort": "asc" if is_time_series else "none"},
            })
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
        elif wants_area:
            suggestions.append({
                "type": "area_chart",
                "title": f"{y_label} Area by {x_label}",
                "config": {"x": dim_col, "y": metric_col},
            })
            suggestions.append({
                "type": "line_chart",
                "title": f"{y_label} Trend by {x_label}" if is_time_series else f"{y_label} by {x_label}",
                "config": {"x": dim_col, "y": metric_col, "sort": "asc" if is_time_series else "none"},
            })
        elif wants_pie or (not is_time_series and not wants_bar and "distribution" in nl_lower and row_count <= 10):
            if row_count <= 10:
                suggestions.append({
                    "type": "pie_chart",
                    "title": f"{y_label} Distribution by {x_label}",
                    "config": {"label": dim_col, "value": metric_col},
                })
            suggestions.append({
                "type": "bar_chart",
                "title": f"{y_label} by {x_label}",
                "config": {"x": dim_col, "y": metric_col, "sort": "desc"},
            })
        elif is_time_series:
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
            suggestions.append({
                "type": "bar_chart",
                "title": f"{y_label} by {x_label}",
                "config": {"x": dim_col, "y": metric_col, "sort": "desc"},
            })
        else:
            suggestions.append({
                "type": "bar_chart",
                "title": f"{y_label} by {x_label}",
                "config": {"x": dim_col, "y": metric_col, "sort": "desc"},
            })
            suggestions.append({
                "type": "pie_chart",
                "title": f"{y_label} Distribution by {x_label}",
                "config": {"label": dim_col, "value": metric_col},
            })

        suggestions.append({"type": "table", "title": "Data Table", "config": {}})
        return suggestions

    def synthesize_data_summary(self, natural_language: str, sql: str, columns: list[str], rows: list[list]) -> str:
        """
        Synthesize exact answer extraction and data summary directly from the executed query
        strictly adhering to EXACT ANSWER EXTRACTION RULES.
        """
        if not rows or not columns:
            return f"Query executed successfully, but returned 0 rows for question: '{natural_language}'."

        sample_rows = rows[:20]
        data_preview = f"Columns: {', '.join(columns)}\nTotal Rows: {len(rows)}\nResults Sample:\n" + "\n".join(str(r) for r in sample_rows)

        system_prompt = (
            "You are the final answer extraction engine for an AI Analytics system.\n"
            "Your job is to answer the user's question EXACTLY AS ASKED using ONLY the validated SQL result and the user's original question.\n\n"
            "## EXACT ANSWER EXTRACTION RULES\n\n"
            "1. EXACT QUESTION COMPLIANCE & MULTI-GROUP VS SINGLE-ENTITY HANDLING\n"
            "- Multi-Group / Grouped Breakdown Questions ('in each', 'per', 'for every', 'by', or queries returning multiple group rows):\n"
            "  * Present the figures for ALL returned groups in the Direct Answer in a clear, concise breakdown list or bullet points.\n"
            "  * NEVER collapse a multi-group result set into only the single top row.\n"
            "- Single-Entity / Global Extremum Questions (e.g. 'Which entity has the single highest...'):\n"
            "  * Explicitly name the single winning entity and its validated metric directly.\n"
            "- If multiple records/entities are tied for the highest or lowest value, list ALL tied entities clearly.\n"
            "- Do NOT answer a similar question, substitute a related metric, invent a metric, or omit a requested metric.\n\n"
            "2. COLUMN SEMANTIC & VALUE ACCURACY\n"
            "- Use the exact figures from the database results. Never fabricate, estimate, or modify numbers.\n"
            "- Represent each metric accurately (e.g. Spent vs Budget vs Remaining Budget vs Salary vs Revenue).\n"
            "- Format numbers properly: currency with '$', percentages with '%', large numbers with commas.\n\n"
            "3. REQUESTED METRIC COMPLETENESS\n"
            "- Ensure all metrics, columns, and calculations requested by the user are clearly presented.\n\n"
            "4. EXACT ANSWER ONLY\n"
            "- Provide:\n"
            "  1. The direct answer naming the entity/entities and primary metrics.\n"
            "  2. The exact values requested by the user.\n"
            "- Do NOT add unrelated insights, speculative explanations, or extraneous commentary.\n\n"
            "Format your output clearly:\n"
            "### 🎯 Direct Answer\n"
            "State the exact direct answer concisely with the entity name(s) and validated figures (listing all groups if a multi-group breakdown was requested).\n\n"
            "### 📊 Key Calculated Values\n"
            "List the specific metrics and calculated figures requested with proper formatting (currency $, %, commas).\n\n"
            "### 🛠️ Execution Trace & Verification\n"
            "- **Intent**: The exact question answered.\n"
            "- **Metrics Verified**: Exact columns/metrics used from live database records."
        )

        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    f"User Request: {natural_language}\n\n"
                    f"Executed SQL Query:\n{sql}\n\n"
                    f"Database Results Data:\n{data_preview}\n\n"
                    "Extract and provide the exact answer:"
                ),
            },
        ]
        summary = self._call_llm(messages, temperature=0.1, max_tokens=700)
        return summary.strip() if summary else f"Query executed successfully ({len(rows)} rows returned)."

    def _extract_schema_details(self, schema_context: str) -> dict[str, list[str]]:
        """Parse tables and their columns from schema context string."""
        tables: dict[str, list[str]] = {}
        for line in schema_context.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"Table:\s*([^\s\[]+)\s*\[(.*)\]", line)
            if m:
                table_name = m.group(1).strip()
                cols_str = m.group(2).strip()
                cols = [c.split("(")[0].strip() for c in cols_str.split(",") if c.strip()]
                tables[table_name] = cols
            else:
                m2 = re.match(r"Table:\s*([^\s\[]+)", line)
                if m2:
                    table_name = m2.group(1).strip()
                    tables[table_name] = []
        return tables

    def analyze_intent_and_clarify(self, natural_language: str, schema_context: str, dialect: str = "SQL") -> dict:
        """
        Dynamic Agentic Intent Analyzer:
        1. Inspects the connected database's live schema dynamically without hardcoded table names.
        2. If the user asks to explore or list tables/schema in the database, returns dynamic exploration cards for the actual tables.
        3. If the user asks any question (specific, broad, analytical, aggregations, filters), returns {"status": "direct"}
           so the system immediately translates the sentence into executable SQL against the verified schema and executes it.
        4. If the question is completely unrelated to any table or column in the database, returns the actual database tables to guide the user.
        """
        if not schema_context or not schema_context.strip():
            return {"status": "direct"}

        prompt_clean = natural_language.strip().lower()
        schema_tables = self._extract_schema_details(schema_context)
        all_table_names = list(schema_tables.keys())
        all_columns = [col.lower() for cols in schema_tables.values() for col in cols]

        # Check if user is asking to explore or list the tables/schema in the database
        is_schema_prompt = (
            any(kw in prompt_clean for kw in ("table", "tables", "schema", "schemas", "database", "structure", "entities"))
            and any(kw in prompt_clean for kw in ("what", "which", "show", "list", "available", "exist", "all", "describe", "tell", "display", "see"))
            and not any(kw in prompt_clean for kw in ("count", "sum", "average", "avg", "highest", "lowest", "calculate", "spent", "salary", "revenue", "price", "cost"))
        )

        if is_schema_prompt and all_table_names:
            formatted_tables = "\n".join(f"- **`{t}`** ({len(schema_tables.get(t, []))} columns)" for t in all_table_names)
            return {
                "status": "clarify",
                "message": (
                    f"Here are the tables available in your connected database:\n\n"
                    f"{formatted_tables}\n\n"
                    f"Select an option below to explore any table or ask a specific question:"
                ),
                "options": [
                    {
                        "id": f"explore_{t.lower()}",
                        "label": f"📊 Explore {t.replace('_', ' ').title()}",
                        "prompt": f"Show summary and top 10 records from {t}",
                        "description": f"Inspect sample records and columns in {t}",
                        "icon": "📊",
                    }
                    for t in all_table_names[:6]
                ],
            }

        # Check if question is completely unrelated (less than 4 words, no match with any table or column)
        words = set(re.findall(r"\b[a-zA-Z]{3,}\b", prompt_clean))
        if len(prompt_clean.split()) <= 3 and all_table_names:
            matches_table = any(t.lower() in prompt_clean or any(w in t.lower() for w in words) for t in all_table_names)
            matches_col = any(c.lower() in prompt_clean or any(w in c.lower() for w in words) for c in all_columns)
            if not matches_table and not matches_col and not any(kw in prompt_clean for kw in ("count", "show", "find", "get", "list", "select", "which", "what", "how", "top", "best", "total")):
                table_list_str = ", ".join(f"`{t}`" for t in all_table_names[:8])
                return {
                    "status": "unrelated",
                    "message": (
                        f"I searched your connected database, but couldn't find data matching '{natural_language}'.\n\n"
                        f"Your database contains the following tables: {table_list_str}.\n"
                        f"You can choose one of the suggestions below or ask about any of these tables:"
                    ),
                    "options": [
                        {
                            "id": f"table_{t}",
                            "label": f"📊 Explore {t.replace('_', ' ').title()}",
                            "prompt": f"Show summary and top records from {t}",
                            "description": f"Overview of data in {t}",
                            "icon": "📊",
                        }
                        for t in all_table_names[:4]
                    ],
                }

        # Proceed directly to sentence analysis, SQL generation, and live execution
        return {"status": "direct"}

    def generate_follow_up_suggestions(
        self, natural_language: str, sql: str, columns: list[str], rows: list[list]
    ) -> list[str]:
        """Generate dynamic, column-driven follow-up suggestions based on the query results."""
        suggestions = []
        nl_lower = natural_language.lower()
        cols = columns or []

        numeric_cols = []
        text_cols = []
        date_cols = []

        for c in cols:
            c_low = c.lower()
            if any(k in c_low for k in ("date", "time", "year", "month", "day", "quarter", "created", "updated", "period")):
                date_cols.append(c)
            elif any(k in c_low for k in ("id", "code", "num", "no")):
                pass
            elif any(k in c_low for k in ("amount", "sum", "total", "avg", "count", "revenue", "profit", "price", "cost", "salary", "expense", "rate", "pct", "margin", "qty", "quantity", "val", "value", "balance", "score")):
                numeric_cols.append(c)
            else:
                text_cols.append(c)

        if numeric_cols and text_cols:
            suggestions.append(f"Show {numeric_cols[0]} breakdown by {text_cols[0]}")
            suggestions.append(f"Show top 5 {text_cols[0]} ranked by {numeric_cols[0]}")
        elif numeric_cols:
            suggestions.append(f"Show highest and lowest records by {numeric_cols[0]}")
            suggestions.append(f"Show summary statistics for {numeric_cols[0]}")

        if date_cols:
            suggestions.append(f"Show trend over time by {date_cols[0]}")

        if text_cols and len(text_cols) >= 2:
            suggestions.append(f"Group records by {text_cols[0]} and {text_cols[1]}")

        if not suggestions:
            suggestions.append("Show summary breakdown for these results")
            suggestions.append("Show top records ranked by key metric")

        unique_suggestions = []
        for s in suggestions:
            if s.lower() != nl_lower and s not in unique_suggestions:
                unique_suggestions.append(s)
            if len(unique_suggestions) >= 4:
                break

        return unique_suggestions


llm_service = LLMService()
