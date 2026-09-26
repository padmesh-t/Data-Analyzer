from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dashboard import Dashboard, DashboardWidget
from app.models.query import Query
from app.schemas.dashboard import (
    DashboardCreateRequest,
    DashboardUpdateRequest,
    LayoutUpdateRequest,
)

import threading


def _run_with_timeout(func, seconds: int):
    """Run func in a thread and return its result, or None on timeout/error.

    Used to bound database network calls (schema fetch, query execution) and
    LLM calls so an unreachable database or a slow model cannot hang or crash
    an auto-generation request. Any failure yields None so the caller can
    degrade gracefully instead of aborting the whole dashboard generation.
    """
    result: dict = {}

    def _target():
        try:
            result["value"] = func()
        except Exception:  # noqa: BLE001
            result["error"] = True

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive() or "error" in result:
        return None
    return result.get("value")


def list_dashboards(
    db: Session, skip: int = 0, limit: int = 50,
    user_id: int | None = None, company_id: int | None = None, include_all: bool = False,
) -> tuple[list[Dashboard], int]:
    query = db.query(Dashboard)
    if company_id is not None:
        query = query.filter(
            (Dashboard.company_id == company_id)
            | ((Dashboard.company_id.is_(None)) & (Dashboard.user_id == user_id))
        )
    elif user_id is not None and not include_all:
        query = query.filter(Dashboard.user_id == user_id)
    total = query.with_entities(func.count(Dashboard.id)).scalar() or 0
    dashboards = (
        query.order_by(Dashboard.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return dashboards, total


def get_dashboard(
    db: Session, dashboard_id: int,
    user_id: int | None = None, company_id: int | None = None, include_all: bool = False,
) -> Dashboard | None:
    query = db.query(Dashboard).filter(Dashboard.id == dashboard_id)
    if company_id is not None:
        query = query.filter(
            (Dashboard.company_id == company_id)
            | ((Dashboard.company_id.is_(None)) & (Dashboard.user_id == user_id))
        )
    elif user_id is not None and not include_all:
        query = query.filter(Dashboard.user_id == user_id)
    return query.first()


def create_dashboard(
    db: Session, data: DashboardCreateRequest, user_id: int, company_id: int | None = None
) -> Dashboard:
    if company_id is None:
        from app.models.user import User
        u = db.query(User).filter(User.id == user_id).first()
        company_id = u.company_id if u else None

    dash = Dashboard(
        user_id=user_id,
        company_id=company_id,
        title=data.title,
        description=data.description,
        is_template=data.is_template,
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return dash


def update_dashboard(
    db: Session, dash: Dashboard, data: DashboardUpdateRequest
) -> Dashboard:
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(dash, key, value)
    db.commit()
    db.refresh(dash)
    return dash


def delete_dashboard(db: Session, dash: Dashboard) -> None:
    db.query(DashboardWidget).filter(
        DashboardWidget.dashboard_id == dash.id
    ).delete()
    db.delete(dash)
    db.commit()


def add_widget(
    db: Session,
    dashboard_id: int,
    widget_type: str,
    title: str,
    position_x: int = 0,
    position_y: int = 0,
    width: int = 6,
    height: int = 4,
    query_id: int | None = None,
    config: dict | None = None,
) -> DashboardWidget:
    widget = DashboardWidget(
        dashboard_id=dashboard_id,
        widget_type=widget_type,
        title=title,
        position_x=position_x,
        position_y=position_y,
        width=width,
        height=height,
        query_id=query_id,
        config=config or {},
    )
    db.add(widget)
    db.commit()
    db.refresh(widget)
    return widget


def update_widget(
    db: Session, widget: DashboardWidget, **updates
) -> DashboardWidget:
    for key, value in updates.items():
        setattr(widget, key, value)
    db.commit()
    db.refresh(widget)
    return widget


def delete_widget(db: Session, widget: DashboardWidget) -> None:
    db.delete(widget)
    db.commit()


def update_layout(
    db: Session, dash: Dashboard, data: LayoutUpdateRequest
) -> list[DashboardWidget]:
    for item in data.widgets:
        db.query(DashboardWidget).filter(
            DashboardWidget.id == item.id,
            DashboardWidget.dashboard_id == dash.id,
        ).update(
            {
                "position_x": item.position_x,
                "position_y": item.position_y,
                "width": item.width,
                "height": item.height,
            }
        )
    db.commit()
    return (
        db.query(DashboardWidget)
        .filter(DashboardWidget.dashboard_id == dash.id)
        .order_by(DashboardWidget.position_y, DashboardWidget.position_x)
        .all()
    )


def auto_generate_from_query(
    db: Session,
    database_id: int,
    query_text: str | None = None,
    template_id: int | None = None,
    user_id: int | None = None,
) -> Dashboard:
    from app.services.connection_service import get_database, get_connector
    from app.services.query_service import (
        _get_schema_context,
        _validate_sql_before_execution,
        _serialize_rows,
    )
    from app.services.llm_service import llm_service
    from app.api.deps import user_has_permission_by_id

    db_conn = get_database(
        db, database_id, user_id=user_id or 0,
        include_all=user_has_permission_by_id(db, user_id or 0, "access.manage"),
    )
    if not db_conn:
        raise ValueError("Database connection not found")

    def _fetch_schema():
        return _get_schema_context(db_conn)

    schema_res = _run_with_timeout(_fetch_schema, 12)
    if schema_res:
        schema_context, table_cols, schema_metadata = schema_res
    else:
        schema_context, table_cols, schema_metadata = "Schema unavailable", {}, {}

    # 1) Decompose the request or automatically analyze schema into a set of widget specs.
    specs = _plan_widgets(query_text, schema_context, db_conn.name if db_conn else "")

    # 2) Derive a readable dashboard title from the request.
    if query_text and query_text.strip():
        clean_q = query_text.strip()
        title = clean_q[:60].capitalize()
        if not title.endswith(("?", ".", "!")):
            title += " Analytics" if not title.lower().endswith("analytics") else ""
        desc = f"AI-generated analytics dashboard focused on '{clean_q}' from {db_conn.name} with pie charts, bar charts, and data tables."
    else:
        title = f"{db_conn.name} Analytics Dashboard" if db_conn else "Auto-generated Dashboard"
        desc = f"AI-generated analytics dashboard analyzing {db_conn.name} database with pie charts, bar charts, and data tables."

    dash = Dashboard(
        user_id=user_id or 0,
        title=title,
        description=desc,
        auto_generated=True,
    )
    db.add(dash)
    db.flush()

    # 3) For each spec: generate SQL, execute read-only, and create a query + widget.
    col = 0
    row = 0
    dialect = llm_service._get_db_dialect(db_conn.connection_type)
    for spec in specs:
        natural_language = spec["question"]
        prompt_nl = f"Write a single executable {dialect} query strictly inside a ```sql ... ``` code block to retrieve data for:\n{natural_language}"
        try:
            generated = _run_with_timeout(
                lambda: llm_service.generate_sql(
                    prompt_nl, schema_context, db_conn.connection_type,
                ),
                12,
            )
        except Exception:
            generated = None
        if not generated:
            sql = _heuristic_sql(natural_language, schema_context, db_conn.connection_type)
            explanation = "Generated from database schema analysis (LLM unavailable)."
        else:
            sql, explanation, _ = generated
        if not sql or sql.strip() in (";", ""):
            sql = _heuristic_sql(natural_language, schema_context, db_conn.connection_type)
            explanation = "Generated from database schema analysis."

        is_valid, sanitized_sql, val_err = _validate_sql_before_execution(
            sql, table_cols, dialect, natural_language=natural_language, schema_metadata=schema_metadata
        )
        if is_valid:
            sql = sanitized_sql
        elif val_err and not val_err.startswith("SECURITY VIOLATION"):
            try:
                fixed = _run_with_timeout(
                    lambda: llm_service.fix_sql(
                        prompt_nl, sql, val_err, schema_context, db_conn.connection_type,
                    ),
                    10,
                )
                if fixed:
                    fixed_sql, fix_explanation, _ = fixed
                    is_valid_after, sanitized_after, _ = _validate_sql_before_execution(
                        fixed_sql, table_cols, dialect, natural_language=natural_language, schema_metadata=schema_metadata
                    )
                    if is_valid_after:
                        sql = sanitized_after
                        explanation = fix_explanation
            except Exception:
                pass

        status = "completed"
        result_columns = None
        result_rows = None
        row_count = 0
        error_message = None
        try:
            def _run_query():
                connector = get_connector(db_conn)
                return connector.execute_query(sql)

            raw = _run_with_timeout(_run_query, 12)
            if raw is None:
                raise TimeoutError("Database query timed out")
            columns = raw.get("columns", [])
            rows = _serialize_rows(raw.get("rows", []))
            result_columns = columns
            result_rows = rows[:1000]
            row_count = len(rows)
        except Exception as e:
            status = "failed"
            error_message = str(e)[:300]

        query = Query(
            user_id=dash.user_id,
            company_id=dash.company_id,
            database_id=database_id,
            natural_language=natural_language,
            generated_sql=sql,
            explanation=explanation,
            status=status,
            result_columns=result_columns,
            result_rows=result_rows,
            row_count=row_count,
            error_message=error_message,
        )
        db.add(query)
        db.flush()

        add_widget(
            db,
            dashboard_id=dash.id,
            widget_type=spec["chart_type"],
            title=spec["title"],
            position_x=col * 6,
            position_y=row * 4,
            width=6,
            height=4,
            query_id=query.id,
            config={
                "auto_generated": True,
                "natural_language": natural_language,
                "database_id": database_id,
                "sql": sql,
            },
        )
        col = 1 - col
        if col == 0:
            row += 1

    db.commit()
    db.refresh(dash)
    return dash


def _plan_widgets(query_text: str | None, schema_context: str, db_name: str = "") -> list[dict]:
    """Turn a request or automatic schema analysis into a rich list of widget specs.

    If query_text is given (e.g. 'salary', 'revenue', 'sales'), plans 4 widgets strictly
    relevant to that topic. If empty, analyzes the whole schema for an executive dashboard.
    """
    import json
    import re
    from app.services.llm_service import llm_service

    is_auto_analysis = not query_text or not query_text.strip()

    if is_auto_analysis:
        prompt = (
            "You are an expert BI data architect. Analyze the provided database schema and design a comprehensive "
            "executive analytics dashboard with 4 distinct visualization widgets. "
            "You MUST include:\n"
            "1. At least one 'pie_chart' for categorical distribution/proportions (e.g. by status, category, department, role).\n"
            "2. At least one 'bar_chart' for metric comparisons/rankings across entities or departments.\n"
            "3. At least one 'table' for top records, details, or overview data.\n"
            "4. An additional relevant chart ('bar_chart', 'pie_chart', or 'table') highlighting key metrics.\n\n"
            "For each widget return a JSON object with:\n"
            "- title: Short descriptive title (e.g. 'Department Headcount', 'Average Salary by Role', 'Top 10 Transactions')\n"
            "- question: Clear natural language question referencing actual tables/columns from schema\n"
            "- chart_type: exactly one of 'pie_chart', 'bar_chart', 'line_chart', 'table', 'kpi'\n\n"
            "Respond with a valid JSON array of objects ONLY, no markdown, no explanation.\n\n"
            f"Database Schema:\n{schema_context[:3000]}"
        )
    else:
        clean_topic = query_text.strip()
        prompt = (
            f"You are an expert BI data architect. The user wants a focused dashboard strictly analyzing: '{clean_topic}'.\n"
            "Analyze the provided database schema and design 4 distinct visualization widgets strictly relevant to this topic/metric.\n"
            "You MUST include a balanced suite:\n"
            f"1. At least one 'pie_chart' showing distributions/proportions relevant to '{clean_topic}' (e.g. breakdown by category, status, department, or role).\n"
            f"2. At least one 'bar_chart' comparing metrics or rankings relevant to '{clean_topic}'.\n"
            f"3. At least one 'table' showing detailed top records or rows relevant to '{clean_topic}'.\n"
            f"4. An additional relevant chart ('bar_chart', 'pie_chart', or 'table') focused on '{clean_topic}'.\n\n"
            "For each widget return a JSON object with:\n"
            f"- title: Short descriptive title strictly related to '{clean_topic}' (e.g. 'Salary by Department', 'Average Salary Ranking', 'Top 10 Salaries')\n"
            "- question: Clear natural language question referencing actual tables/columns from schema\n"
            "- chart_type: exactly one of 'pie_chart', 'bar_chart', 'line_chart', 'table', 'kpi'\n\n"
            "Respond with a valid JSON array of objects ONLY, no markdown, no explanation.\n\n"
            f"Database Schema:\n{schema_context[:3000]}"
        )

    raw = None
    if not llm_service.use_mock:
        try:
            raw = llm_service._call_vllm(
                [
                    {"role": "system", "content": "You output strictly JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1024,
            )
        except Exception:
            raw = None

    if raw:
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list) and len(parsed) >= 2:
                specs = []
                for item in parsed[:6]:
                    if not isinstance(item, dict):
                        continue
                    q = (item.get("question") or item.get("title") or "").strip()
                    if not q:
                        continue
                    specs.append({
                        "title": item.get("title") or q[:40],
                        "question": q,
                        "chart_type": _normalize_chart_type(item.get("chart_type")),
                    })
                if len(specs) >= 2:
                    types = {s["chart_type"] for s in specs}
                    if "pie_chart" not in types and len(specs) > 0:
                        specs[0]["chart_type"] = "pie_chart"
                    if "bar_chart" not in types and len(specs) > 1:
                        specs[1]["chart_type"] = "bar_chart"
                    if "table" not in types and len(specs) > 2:
                        specs[2]["chart_type"] = "table"
                    return specs
        except (json.JSONDecodeError, TypeError):
            pass

    # Heuristic fallback: dynamically extract schema tables and columns
    return _heuristic_schema_plan(schema_context, query_text)


def _heuristic_schema_plan(schema_context: str, query_text: str | None = None) -> list[dict]:
    """Dynamically parse schema_context to build Pie Chart, Bar Chart, and Data Table widgets.
    
    If query_text is given (e.g. 'salary', 'revenue', 'sales'), prioritizes tables and columns
    matching the query keywords so the generated widgets strictly focus on that topic.
    If query_text is empty, analyzes the whole database schema for a comprehensive executive view.
    """
    import re

    table_matches = re.findall(r"Table:\s*(\w+)\s*\[([^\]]*)\]", schema_context)
    tables_info = []
    for t_name, cols_raw in table_matches:
        col_list = []
        for c in cols_raw.split(","):
            c_clean = c.strip()
            if not c_clean:
                continue
            parts = c_clean.split(" ")
            c_name = parts[0]
            c_type = parts[1] if len(parts) > 1 else ""
            col_list.append((c_name, c_type))
        tables_info.append((t_name, col_list))

    if not tables_info:
        return [
            {"title": "Overview", "question": query_text or "Show overview data", "chart_type": "table"}
        ]

    q_clean = (query_text or "").strip().lower()
    keywords = [w for w in re.findall(r"\w+", q_clean) if len(w) > 2] if q_clean else []

    # Pick the best table matching query_text or default to primary table
    primary_table, primary_cols = tables_info[0]
    best_score = -1

    for t_name, cols in tables_info:
        t_low = t_name.lower()
        col_names_low = [c[0].lower() for c in cols]
        score = 0

        if keywords:
            for kw in keywords:
                if kw in t_low:
                    score += 10
                for c_low in col_names_low:
                    if kw in c_low:
                        score += 5
        else:
            if any(k in t_low for k in ("employee", "sale", "order", "product", "user", "customer", "transaction", "item")):
                score += 5

        if score > best_score:
            best_score = score
            primary_table, primary_cols = t_name, cols

    col_names = [c[0] for c in primary_cols]

    # 1. Find numeric / metric column (prioritize keyword matches if query_text was provided)
    num_col = None
    if keywords:
        for kw in keywords:
            matched = next((c for c in col_names if kw in c.lower()), None)
            if matched:
                num_col = matched
                break

    if not num_col:
        num_col = next((c for c in col_names if any(k in c.lower() for k in ("salary", "amount", "price", "total", "revenue", "sales", "qty", "quantity", "cost", "balance", "rate", "score", "val"))), None)
    if not num_col:
        num_col = next((c for c in col_names if any(k in c.lower() for k in ("id", "count", "num")) and not c.lower().endswith("_id")), None)

    # 2. Find categorical column for Pie Chart & Bar Chart (prioritize keyword matches if not already used)
    cat_col = None
    if keywords:
        for kw in keywords:
            matched = next((c for c in col_names if kw in c.lower() and c != num_col), None)
            if matched:
                cat_col = matched
                break

    if not cat_col:
        cat_col = next((c for c in col_names if c != num_col and any(k in c.lower() for k in ("department", "dept", "category", "status", "role", "type", "gender", "country", "city", "brand", "state", "team", "division"))), None)
    if not cat_col:
        cat_col = next((c for c in col_names if c != num_col and (c.lower().endswith("_id") or "name" in c.lower())), col_names[0] if col_names else "CATEGORY")

    # 3. Find secondary categorical column
    secondary_cat = next((c for c in col_names if c != cat_col and c != num_col and any(k in c.lower() for k in ("status", "type", "role", "department", "city", "country", "name", "category"))), None)

    topic_label = num_col or (keywords[0].title() if keywords else primary_table)
    specs = []

    # 1. PIE CHART - Distribution / Breakdown
    pie_cat = secondary_cat or cat_col
    if num_col:
        specs.append({
            "title": f"{topic_label} Distribution by {pie_cat}".replace("_", " ").title(),
            "question": f"Show {topic_label} distribution grouped by {pie_cat} in {primary_table} as a pie chart",
            "chart_type": "pie_chart",
        })
    else:
        specs.append({
            "title": f"{primary_table} by {pie_cat}".replace("_", " ").title(),
            "question": f"Show the distribution and breakdown of {primary_table} by {pie_cat} as a pie chart",
            "chart_type": "pie_chart",
        })

    # 2. BAR CHART - Metric comparison by Category
    if num_col and cat_col:
        specs.append({
            "title": f"Average {topic_label} by {cat_col}".replace("_", " ").title(),
            "question": f"Show average {num_col} by {cat_col} in {primary_table} as a bar chart",
            "chart_type": "bar_chart",
        })
    else:
        specs.append({
            "title": f"{primary_table} Volume Comparison".replace("_", " ").title(),
            "question": f"Show count of {primary_table} grouped by {cat_col} as a bar chart",
            "chart_type": "bar_chart",
        })

    # 3. BAR CHART - Ranking or Multi-table
    if num_col:
        specs.append({
            "title": f"Top {topic_label} Ranking".replace("_", " ").title(),
            "question": f"Show highest {num_col} records in {primary_table} as a bar chart",
            "chart_type": "bar_chart",
        })
    elif len(tables_info) > 1 and tables_info[1][0] != primary_table:
        sec_table, sec_cols = tables_info[1]
        sec_col_names = [c[0] for c in sec_cols]
        sec_cat = next((c for c in sec_col_names if any(k in c.lower() for k in ("name", "status", "category", "type"))), sec_col_names[0] if sec_col_names else "ID")
        specs.append({
            "title": f"{sec_table} Breakdown".replace("_", " ").title(),
            "question": f"Show count of records in {sec_table} grouped by {sec_cat} as a bar chart",
            "chart_type": "bar_chart",
        })
    else:
        specs.append({
            "title": f"{primary_table} Summary".replace("_", " ").title(),
            "question": f"Show count of records in {primary_table} as a bar chart",
            "chart_type": "bar_chart",
        })

    # 4. DATA TABLE - Detailed top records / Overview
    if num_col:
        specs.append({
            "title": f"Top 10 {primary_table} by {topic_label}".replace("_", " ").title(),
            "question": f"Show top 10 rows from {primary_table} ordered by {num_col} descending",
            "chart_type": "table",
        })
    else:
        specs.append({
            "title": f"{primary_table} Overview Table".replace("_", " ").title(),
            "question": f"Show recent rows from {primary_table} table",
            "chart_type": "table",
        })

    return specs


def _normalize_chart_type(value: str | None) -> str:
    allowed = {"bar_chart", "line_chart", "pie_chart", "area_chart", "kpi", "table"}
    if value in allowed:
        return value
    return _infer_chart_type(value or "")


def _infer_chart_type(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["pie", "share", "proportion"]):
        return "pie_chart"
    if any(k in t for k in ["per day", "over time", "trend", "daily", "monthly", "timeline", "by date"]):
        return "line_chart"
    if any(k in t for k in ["distribution", "percentage", "breakdown", "by", "per ", "per role", "per category", "ranking", "compare"]):
        return "bar_chart"
    if any(k in t for k in ["total", "sum", "count", "number of", "no of", "amount", "revenue"]):
        return "kpi"
    return "table"


def _get_empty_query_response(connection_type: str = "postgresql") -> str:
    """Return an empty query formatted for the specified database dialect."""
    c_type = (connection_type or "").lower()
    if "oracle" in c_type:
        return "SELECT 1 FROM DUAL WHERE 1=0"
    return "SELECT 1 WHERE 1=0"


def _heuristic_sql(question: str, schema_context: str, connection_type: str = "postgresql") -> str:
    """Best-effort SQL when the LLM is unavailable or times out."""
    import re

    c_type = (connection_type or "").lower()
    is_oracle = "oracle" in c_type
    is_sqlserver = "sqlserver" in c_type or "mssql" in c_type

    table_matches = re.findall(r"Table:\s*(\w+)\s*\[([^\]]*)\]", schema_context)
    if not table_matches:
        tables = re.findall(r"Table:\s*(\w+)", schema_context)
        if not tables:
            return _get_empty_query_response(connection_type)
        table = tables[0]
        all_cols = []
    else:
        table = table_matches[0][0]
        # Check if question mentions a specific table
        for t_name, cols_raw in table_matches:
            if t_name.lower() in question.lower():
                table = t_name
                break
        cols_raw = next((c for t, c in table_matches if t == table), table_matches[0][1])
        all_cols = [c.strip().split(" ")[0] for c in cols_raw.split(",") if c.strip()]

    q = question.lower()

    date_col = next((c for c in all_cols if any(k in c.lower() for k in ("date", "time", "created", "at", "hire"))), None)
    amount_col = next((c for c in all_cols if any(k in c.lower() for k in ("salary", "amount", "price", "total", "revenue", "value", "sum", "cost", "sales", "qty"))), None)
    cat_col = next((c for c in all_cols if any(k in c.lower() for k in ("department", "dept", "category", "role", "status", "type", "gender", "city", "country", "brand", "state"))), None)
    if not cat_col and all_cols:
        cat_col = next((c for c in all_cols if "name" in c.lower() or c.lower().endswith("_id")), all_cols[0])

    # 1. Timeline / Trend queries
    if any(k in q for k in ["per day", "daily", "over time", "trend", "by date", "timeline"]) and date_col:
        return f"SELECT {date_col}, COUNT(*) AS count FROM {table} GROUP BY {date_col} ORDER BY {date_col}"

    # 2. Metric aggregate by Category (Bar Chart / Distribution)
    if ("by" in q or "per" in q) and cat_col:
        if amount_col:
            if "avg" in q or "average" in q:
                return f"SELECT {cat_col}, ROUND(AVG({amount_col}), 2) AS avg_{amount_col.lower()} FROM {table} GROUP BY {cat_col} ORDER BY avg_{amount_col.lower()} DESC"
            elif "max" in q or "highest" in q:
                return f"SELECT {cat_col}, MAX({amount_col}) AS highest_{amount_col.lower()} FROM {table} GROUP BY {cat_col} ORDER BY highest_{amount_col.lower()} DESC"
            return f"SELECT {cat_col}, SUM({amount_col}) AS total_{amount_col.lower()} FROM {table} GROUP BY {cat_col} ORDER BY total_{amount_col.lower()} DESC"
        return f"SELECT {cat_col}, COUNT(*) AS total_count FROM {table} GROUP BY {cat_col} ORDER BY total_count DESC"

    # 3. Pie Chart / Distribution
    if any(k in q for k in ["pie", "distribution", "breakdown", "proportion", "share"]) and cat_col:
        return f"SELECT {cat_col}, COUNT(*) AS total_count FROM {table} GROUP BY {cat_col} ORDER BY total_count DESC"

    # 4. Total / KPI queries
    if any(k in q for k in ["total", "sum", "amount", "revenue", "average", "avg"]) and amount_col:
        if "avg" in q or "average" in q:
            return f"SELECT ROUND(AVG({amount_col}), 2) AS avg_{amount_col.lower()} FROM {table}"
        return f"SELECT SUM({amount_col}) AS total FROM {table}"

    if "count" in q or "number of" in q or "no of" in q:
        return f"SELECT COUNT(*) AS count FROM {table}"

    limit_match = re.search(r"\b(?:top|best|first)\s+(\d+)\b", q)
    limit_num = int(limit_match.group(1)) if limit_match else (10 if "top 10" in q else 100)

    # 5. Domain specific join heuristics (e.g. restaurant menu & order items)
    sc_low = schema_context.lower()
    if any(k in q for k in ("selling", "dish", "dishes", "food", "menu")) and "menu_items" in sc_low and "order_items" in sc_low:
        if is_sqlserver:
            return f"SELECT TOP {limit_num} mi.name, SUM(oi.quantity) AS total_sold FROM menu_items mi JOIN order_items oi ON mi.id = oi.menu_item_id GROUP BY mi.id, mi.name ORDER BY total_sold DESC"
        elif is_oracle:
            return f"SELECT mi.name, SUM(oi.quantity) AS total_sold FROM menu_items mi JOIN order_items oi ON mi.id = oi.menu_item_id GROUP BY mi.id, mi.name ORDER BY total_sold DESC FETCH FIRST {limit_num} ROWS ONLY"
        return f"SELECT mi.name, SUM(oi.quantity) AS total_sold FROM menu_items mi JOIN order_items oi ON mi.id = oi.menu_item_id GROUP BY mi.id, mi.name ORDER BY total_sold DESC LIMIT {limit_num}"

    # 6. Top N / Table queries / Default fallback
    if is_sqlserver:
        return f"SELECT TOP {limit_num} * FROM {table}"
    elif is_oracle:
        return f"SELECT * FROM {table} FETCH FIRST {limit_num} ROWS ONLY"
    return f"SELECT * FROM {table} LIMIT {limit_num}"

