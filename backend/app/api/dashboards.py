from fastapi import APIRouter, Depends, HTTPException, Query as FastAPIQuery
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.dashboard import DashboardWidget
from app.models.query import Query
from app.schemas.dashboard import (
    DashboardCreateRequest,
    DashboardUpdateRequest,
    DashboardDetailResponse,
    DashboardResponse,
    DashboardListResponse,
    WidgetResponse,
    LayoutUpdateRequest,
    AutoGenerateRequest,
    WidgetConfig,
)
from app.services import dashboard_service

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _dashboard_to_detail(dash) -> DashboardDetailResponse:
    return DashboardDetailResponse(
        id=dash.id,
        title=dash.title,
        description=dash.description,
        layout_config=dash.layout_config,
        is_template=dash.is_template,
        is_public=dash.is_public,
        auto_generated=dash.auto_generated,
        widgets=[WidgetResponse.model_validate(w) for w in (dash.widgets or [])],
        created_at=dash.created_at,
        updated_at=dash.updated_at,
    )


def _dashboard_to_list(dash) -> DashboardResponse:
    return DashboardResponse(
        id=dash.id,
        title=dash.title,
        description=dash.description,
        layout_config=dash.layout_config,
        is_template=dash.is_template,
        is_public=dash.is_public,
        auto_generated=dash.auto_generated,
        widget_count=len(dash.widgets) if hasattr(dash, "widgets") and dash.widgets else 0,
        created_at=dash.created_at,
        updated_at=dash.updated_at,
    )


@router.get("", response_model=DashboardListResponse)
def list_dashboards(
    skip: int = FastAPIQuery(0, ge=0),
    limit: int = FastAPIQuery(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dashboards, total = dashboard_service.list_dashboards(db, skip, limit)
    items = [_dashboard_to_list(d) for d in dashboards]
    return DashboardListResponse(dashboards=items, total=total)


@router.get("/{dashboard_id}", response_model=DashboardDetailResponse)
def get_dashboard(
    dashboard_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.get_dashboard(db, dashboard_id)
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return _dashboard_to_detail(dash)


@router.post("", response_model=DashboardDetailResponse, status_code=201)
def create_dashboard(
    data: DashboardCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.create_dashboard(db, data, user_id=current_user.id)
    return _dashboard_to_detail(dash)


@router.put("/{dashboard_id}", response_model=DashboardDetailResponse)
def update_dashboard(
    dashboard_id: int,
    data: DashboardUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.get_dashboard(db, dashboard_id)
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    dash = dashboard_service.update_dashboard(db, dash, data)
    return _dashboard_to_detail(dash)


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(
    dashboard_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.get_dashboard(db, dashboard_id)
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    dashboard_service.delete_dashboard(db, dash)


@router.post("/{dashboard_id}/widgets", response_model=WidgetResponse, status_code=201)
def add_widget(
    dashboard_id: int,
    data: WidgetConfig,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.get_dashboard(db, dashboard_id)
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    config = data.config or {}
    if data.query_id and not config.get("sql"):
        q = db.query(Query).filter(Query.id == data.query_id).first()
        if q and q.generated_sql:
            config["sql"] = q.generated_sql
            config["database_id"] = q.database_id
    widget = dashboard_service.add_widget(
        db,
        dashboard_id=dashboard_id,
        widget_type=data.widget_type,
        title=data.title,
        position_x=data.position_x,
        position_y=data.position_y,
        width=data.width,
        height=data.height,
        query_id=data.query_id,
        config=config,
    )
    return WidgetResponse.model_validate(widget)


@router.put("/{dashboard_id}/widgets/{widget_id}", response_model=WidgetResponse)
def update_widget(
    dashboard_id: int,
    widget_id: int,
    data: WidgetConfig,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.get_dashboard(db, dashboard_id)
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    widget = (
        db.query(DashboardWidget)
        .filter(
            DashboardWidget.id == widget_id,
            DashboardWidget.dashboard_id == dashboard_id,
        )
        .first()
    )
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    config = data.config or {}
    if data.query_id and not config.get("sql"):
        q = db.query(Query).filter(Query.id == data.query_id).first()
        if q and q.generated_sql:
            config["sql"] = q.generated_sql
            config["database_id"] = q.database_id
    widget = dashboard_service.update_widget(
        db,
        widget,
        widget_type=data.widget_type,
        title=data.title,
        position_x=data.position_x,
        position_y=data.position_y,
        width=data.width,
        height=data.height,
        query_id=data.query_id,
        config=config,
    )
    return WidgetResponse.model_validate(widget)


@router.delete("/{dashboard_id}/widgets/{widget_id}", status_code=204)
def delete_widget(
    dashboard_id: int,
    widget_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.get_dashboard(db, dashboard_id)
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    widget = (
        db.query(DashboardWidget)
        .filter(
            DashboardWidget.id == widget_id,
            DashboardWidget.dashboard_id == dashboard_id,
        )
        .first()
    )
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    dashboard_service.delete_widget(db, widget)


@router.put("/{dashboard_id}/layout", response_model=list[WidgetResponse])
def update_layout(
    dashboard_id: int,
    data: LayoutUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.get_dashboard(db, dashboard_id)
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    widgets = dashboard_service.update_layout(db, dash, data)
    return [WidgetResponse.model_validate(w) for w in widgets]


@router.post("/auto-generate", response_model=DashboardDetailResponse, status_code=201)
def auto_generate_dashboard(
    data: AutoGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    dash = dashboard_service.auto_generate_from_query(
        db, database_id=data.database_id, query_text=data.query_text,
        user_id=current_user.id,
    )
    return _dashboard_to_detail(dash)
