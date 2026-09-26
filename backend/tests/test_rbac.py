import pytest

from app.api.deps import user_has_permission
from tests.conftest import auth_headers


class TestRBACPermissions:
    def test_user_without_roles_has_no_permissions(self, db_session, plain_user):
        assert user_has_permission(db_session, plain_user, "database.read") is False

    def test_user_with_role_holding_permission_has_it(self, db_session, analyst):
        assert user_has_permission(db_session, analyst, "database.read") is True

    def test_user_lacking_permission_denied(self, db_session, analyst):
        assert user_has_permission(db_session, analyst, "access.manage") is False

    def test_admin_has_access_manage(self, db_session, admin):
        assert user_has_permission(db_session, admin, "access.manage") is True


class TestRBACApiGates:
    def test_unauthenticated_gets_401_or_403(self, client):
        r = client.get("/api/v1/connections")
        assert r.status_code in (401, 403)

    def test_plain_user_cannot_list_connections(self, client, plain_user):
        r = client.get("/api/v1/connections", headers=auth_headers(plain_user))
        assert r.status_code == 403
        assert "Insufficient permissions" in r.json()["detail"]

    def test_analyst_can_list_own_connections(self, client, analyst, owned_connection):
        r = client.get("/api/v1/connections", headers=auth_headers(analyst))
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_analyst_cannot_assign_roles(self, client, analyst, admin):
        r = client.post(
            f"/api/v1/users/{admin.id}/roles",
            json={"role_id": 1},
            headers=auth_headers(analyst),
        )
        assert r.status_code == 403

    def test_analyst_cannot_read_audit_logs(self, client, analyst):
        r = client.get("/api/v1/audit/logs", headers=auth_headers(analyst))
        assert r.status_code == 403

    def test_admin_can_read_audit_logs(self, client, admin):
        r = client.get("/api/v1/audit/logs", headers=auth_headers(admin))
        assert r.status_code == 200

    def test_analyst_cannot_create_database(self, client, analyst):
        r = client.post(
            "/api/v1/connections",
            json={
                "name": "x", "connection_type": "postgresql", "host": "h",
                "port": 5432, "database_name": "d", "username": "u", "password": "p",
            },
            headers=auth_headers(analyst),
        )
        assert r.status_code == 403

    def test_admin_can_create_database(self, client, admin):
        r = client.post(
            "/api/v1/connections",
            json={
                "name": "x", "connection_type": "postgresql", "host": "h",
                "port": 5432, "database_name": "d", "username": "u", "password": "p",
            },
            headers=auth_headers(admin),
        )
        assert r.status_code == 201

    def test_admin_can_create_user_with_roles(self, client, admin):
        r = client.post(
            "/api/v1/users",
            json={
                "full_name": "New Team Member",
                "email": "newuser@example.com",
                "password": "strongPassword123!",
                "roles": ["Admin"],
            },
            headers=auth_headers(admin),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["email"] == "newuser@example.com"
        assert any(role["name"] == "Admin" for role in data["roles"])

    def test_analyst_cannot_create_user(self, client, analyst):
        r = client.post(
            "/api/v1/users",
            json={
                "full_name": "Unauthorized User",
                "email": "unauth@example.com",
                "password": "password123",
            },
            headers=auth_headers(analyst),
        )
        assert r.status_code == 403


class TestSuperAdminHierarchyAndImmunity:
    def test_admin_cannot_deactivate_superadmin(self, client, admin, company_admin):
        r = client.post(
            f"/api/v1/users/{admin.id}/deactivate",
            headers=auth_headers(company_admin),
        )
        assert r.status_code == 403
        assert "cannot deactivate the company superadmin" in r.json()["detail"].lower()

    def test_admin_cannot_delete_superadmin(self, client, admin, company_admin):
        r = client.delete(
            f"/api/v1/users/{admin.id}",
            headers=auth_headers(company_admin),
        )
        assert r.status_code == 403
        assert "cannot delete the company superadmin" in r.json()["detail"].lower()

    def test_admin_cannot_assign_superadmin_role(self, client, company_admin, analyst, permissions, db_session):
        from app.models.role import Role
        superadmin_role = db_session.query(Role).filter(Role.name == "SuperAdmin").first()
        r = client.post(
            f"/api/v1/users/{analyst.id}/roles",
            json={"role_id": superadmin_role.id},
            headers=auth_headers(company_admin),
        )
        assert r.status_code == 403
        assert "superadmin role cannot be assigned" in r.json()["detail"].lower()

    def test_admin_cannot_modify_superadmin_roles(self, client, admin, company_admin, permissions, db_session):
        from app.models.role import Role
        superadmin_role = db_session.query(Role).filter(Role.name == "SuperAdmin").first()
        r = client.delete(
            f"/api/v1/users/{admin.id}/roles/{superadmin_role.id}",
            headers=auth_headers(company_admin),
        )
        assert r.status_code == 403

    def test_admin_can_deactivate_regular_user(self, client, company_admin, analyst):
        r = client.post(
            f"/api/v1/users/{analyst.id}/deactivate",
            headers=auth_headers(company_admin),
        )
        assert r.status_code == 200
        assert r.json()["message"] == "User deactivated"

    def test_superadmin_can_deactivate_admin(self, client, admin, company_admin):
        r = client.post(
            f"/api/v1/users/{company_admin.id}/deactivate",
            headers=auth_headers(admin),
        )
        assert r.status_code == 200
        assert r.json()["message"] == "User deactivated"


class TestSystemRoleProtection:
    def test_system_role_cannot_be_deleted(self, client, admin, db_session):
        from app.models.role import Role

        role = Role(name="SystemRole", description="x", is_system=True)
        db_session.add(role)
        db_session.commit()
        r = client.delete(f"/api/v1/roles/{role.id}", headers=auth_headers(admin))
        assert r.status_code == 400
        assert "System roles" in r.json()["detail"]

    def test_system_role_cannot_be_modified(self, client, admin, db_session):
        from app.models.role import Role

        role = Role(name="SystemRole2", description="x", is_system=True)
        db_session.add(role)
        db_session.commit()
        r = client.put(
            f"/api/v1/roles/{role.id}",
            json={"name": "Renamed"},
            headers=auth_headers(admin),
        )
        assert r.status_code == 400


class TestUserSelfProtection:
    def test_user_cannot_deactivate_own_account(self, client, admin):
        r = client.post(
            f"/api/v1/users/{admin.id}/deactivate",
            headers=auth_headers(admin),
        )
        assert r.status_code == 400
        assert "cannot deactivate your own account" in r.json()["detail"].lower()

    def test_user_cannot_delete_own_account(self, client, admin):
        r = client.delete(
            f"/api/v1/users/{admin.id}",
            headers=auth_headers(admin),
        )
        assert r.status_code == 400
        assert "cannot delete your own account" in r.json()["detail"].lower()

    def test_user_cannot_modify_own_roles(self, client, admin, db_session):
        from app.models.role import Role
        role = db_session.query(Role).filter(Role.name == "Analyst").first()
        r = client.put(
            f"/api/v1/users/{admin.id}/roles",
            json={"role_id": role.id},
            headers=auth_headers(admin),
        )
        assert r.status_code == 400
        assert "cannot modify your own roles" in r.json()["detail"].lower()

        r_post = client.post(
            f"/api/v1/users/{admin.id}/roles",
            json={"role_id": role.id},
            headers=auth_headers(admin),
        )
        assert r_post.status_code == 400
        assert "cannot modify your own roles" in r_post.json()["detail"].lower()

        r_del = client.delete(
            f"/api/v1/users/{admin.id}/roles/{role.id}",
            headers=auth_headers(admin),
        )
        assert r_del.status_code == 400
        assert "cannot modify your own roles" in r_del.json()["detail"].lower()

    def test_admin_can_set_role_for_another_user_atomically(self, client, admin, plain_user, db_session):
        from app.models.role import Role
        analyst_role = db_session.query(Role).filter(Role.name == "Analyst").first()
        r = client.put(
            f"/api/v1/users/{plain_user.id}/roles",
            json={"role_id": analyst_role.id},
            headers=auth_headers(admin),
        )
        assert r.status_code == 200
        assert "Role updated to" in r.json()["message"]


class TestQueryAccessRBAC:
    def test_analyst_can_access_query_in_same_company(self, client, db_session, admin, analyst, owned_connection):
        from app.models.query import Query
        from app.models.company import Company
        
        company = Company(name="Acme Corp")
        db_session.add(company)
        db_session.commit()
        
        admin.company_id = company.id
        analyst.company_id = company.id
        db_session.commit()
        
        q = Query(
            user_id=admin.id,
            company_id=company.id,
            database_id=owned_connection.id,
            natural_language="Total sales",
            generated_sql="SELECT sum(amount) FROM sales;",
            status="completed",
            result_columns=["total"],
            result_rows=[[1000]],
            row_count=1,
        )
        db_session.add(q)
        db_session.commit()
        
        r = client.get(f"/api/v1/queries/{q.id}", headers=auth_headers(analyst))
        assert r.status_code == 200
        assert r.json()["id"] == q.id
        assert r.json()["results"]["rows"] == [[1000]]

    def test_viewer_can_access_query_attached_to_dashboard_widget(self, client, db_session, admin, viewer, owned_connection):
        from app.models.query import Query
        from app.models.dashboard import Dashboard, DashboardWidget
        
        q = Query(
            user_id=admin.id,
            database_id=owned_connection.id,
            natural_language="User signups",
            generated_sql="SELECT count(*) FROM users;",
            status="completed",
            result_columns=["count"],
            result_rows=[[42]],
            row_count=1,
        )
        db_session.add(q)
        db_session.commit()
        
        dash = Dashboard(
            title="Public Metrics",
            user_id=admin.id,
            is_public=True,
        )
        db_session.add(dash)
        db_session.commit()
        
        widget = DashboardWidget(
            dashboard_id=dash.id,
            query_id=q.id,
            title="Signups Widget",
            widget_type="kpi",
            position_x=0,
            position_y=0,
            width=6,
            height=4,
            config={},
        )
        db_session.add(widget)
        db_session.commit()
        
        r = client.get(f"/api/v1/queries/{q.id}", headers=auth_headers(viewer))
        assert r.status_code == 200
        assert r.json()["id"] == q.id
        assert r.json()["results"]["rows"] == [[42]]

    def test_viewer_denied_access_to_unrelated_private_query(self, client, db_session, admin, viewer, owned_connection):
        from app.models.query import Query
        
        q = Query(
            user_id=admin.id,
            database_id=owned_connection.id,
            natural_language="Secret Admin Query",
            generated_sql="SELECT * FROM secrets;",
            status="completed",
            result_columns=["secret"],
            result_rows=[["classified"]],
            row_count=1,
        )
        db_session.add(q)
        db_session.commit()
        
        r = client.get(f"/api/v1/queries/{q.id}", headers=auth_headers(viewer))
        assert r.status_code == 404



