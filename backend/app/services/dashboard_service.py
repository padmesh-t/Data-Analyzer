from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dashboard import Dashboard, DashboardWidget
from app.models.query import Query
from app.schemas.dashboard import (
    DashboardCreateRequest,
    DashboardUpdateRequest,
    LayoutUpdateRequest,
)
from app.models.template import QueryTemplate


def list_dashboards(
    db: Session, skip: int = 0, limit: int = 50
) -> tuple[list[Dashboard], int]:
    total = db.query(func.count(Dashboard.id)).scalar() or 0
    dashboards = (
        db.query(Dashboard)
        .order_by(Dashboard.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return dashboards, total


def get_dashboard(db: Session, dashboard_id: int) -> Dashboard | None:
    return db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()


def create_dashboard(
    db: Session, data: DashboardCreateRequest, user_id: int
) -> Dashboard:
    dash = Dashboard(
        user_id=user_id,
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
    if template_id:
        template = (
            db.query(QueryTemplate).filter(QueryTemplate.id == template_id).first()
        )
    else:
        template = (
            db.query(QueryTemplate)
            .filter(QueryTemplate.database_id == database_id)
            .order_by(QueryTemplate.created_at.desc())
            .first()
        )

    dash = Dashboard(
        user_id=user_id or 0,
        title=f"Auto-generated Dashboard {'from ' + query_text[:50] if query_text else 'from top queries'}",
        description="Automatically generated dashboard based on query analysis",
        auto_generated=True,
    )
    db.add(dash)
    db.flush()

    if template:
        query = Query(
            user_id=dash.user_id,
            database_id=template.database_id,
            natural_language=template.natural_language,
            generated_sql=template.generated_sql,
            status="completed",
        )
        db.add(query)
        db.flush()
        add_widget(
            db,
            dashboard_id=dash.id,
            widget_type="kpi",
            title=template.title,
            query_id=query.id,
            config={
                "template_id": template.id,
                "database_id": template.database_id,
            },
        )
    else:
        add_widget(
            db,
            dashboard_id=dash.id,
            widget_type="table",
            title="Query Results",
            config={
                "message": "No templates found for auto-generation",
            },
        )

    db.commit()
    db.refresh(dash)
    return dash
