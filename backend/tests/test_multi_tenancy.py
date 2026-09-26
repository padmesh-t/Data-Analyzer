import pytest
from app.models.company import Company
from app.models.user import User, UserRole
from app.models.role import Role
from app.models.connection import DatabaseConnection
from tests.conftest import auth_headers, _make_role


class TestMultiTenancyIsolation:
    def test_registration_creates_company_and_superadmin(self, client, permissions, db_session):
        # Create SuperAdmin system role
        _make_role(db_session, "SuperAdmin", list(permissions.values()), is_system=True)

        # 1. Register User A
        resp_a = client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner_a@company-a.com",
                "password": "Password123!",
                "full_name": "Alice Admin",
                "company_name": "Alpha Corp",
            },
        )
        assert resp_a.status_code == 201
        user_a_data = resp_a.json()["user"]
        assert user_a_data["company_name"] == "Alpha Corp"
        assert user_a_data["company_id"] is not None
        assert any(r["name"] == "SuperAdmin" for r in user_a_data["roles"])

        # 2. Register User B
        resp_b = client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner_b@company-b.com",
                "password": "Password123!",
                "full_name": "Bob Admin",
                "company_name": "Beta LLC",
            },
        )
        assert resp_b.status_code == 201
        user_b_data = resp_b.json()["user"]
        assert user_b_data["company_name"] == "Beta LLC"
        assert user_b_data["company_id"] is not None
        assert user_b_data["company_id"] != user_a_data["company_id"]

    def test_database_isolation_between_companies(self, client, db_session, permissions):
        role = _make_role(db_session, "SuperAdmin", list(permissions.values()), is_system=True)

        # Create company A and company B
        comp_a = Company(name="Company A")
        comp_b = Company(name="Company B")
        db_session.add_all([comp_a, comp_b])
        db_session.commit()

        # User A in Company A
        user_a = User(email="admin_a@comp-a.com", password_hash="hash", full_name="Admin A", company_id=comp_a.id, is_active=True)
        # User B in Company B
        user_b = User(email="admin_b@comp-b.com", password_hash="hash", full_name="Admin B", company_id=comp_b.id, is_active=True)
        db_session.add_all([user_a, user_b])
        db_session.commit()

        db_session.add(UserRole(user_id=user_a.id, role_id=role.id))
        db_session.add(UserRole(user_id=user_b.id, role_id=role.id))
        db_session.commit()

        # Database connection in Company A
        conn_a = DatabaseConnection(
            name="Alpha DB", connection_type="postgresql", host="localhost",
            port=5432, database_name="alpha", username="user",
            password="pass", created_by=user_a.id, company_id=comp_a.id,
        )
        db_session.add(conn_a)
        db_session.commit()

        # User B lists databases -> should not see Company A's database
        headers_b = auth_headers(user_b)
        resp = client.get("/api/v1/connections", headers=headers_b)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # User A lists databases -> should see Company A's database
        headers_a = auth_headers(user_a)
        resp = client.get("/api/v1/connections", headers=headers_a)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["connections"][0]["name"] == "Alpha DB"

    def test_admin_creates_sub_user_in_same_company(self, client, db_session, permissions):
        # Create SuperAdmin and Analyst roles
        _make_role(db_session, "SuperAdmin", list(permissions.values()), is_system=True)
        _make_role(db_session, "Analyst", [
            permissions["database.read"],
            permissions["query.read"],
            permissions["query.execute"],
            permissions["dashboard.read"],
            permissions["dashboard.create"],
        ], is_system=True)

        # Register Admin A
        resp_a = client.post(
            "/api/v1/auth/register",
            json={
                "email": "corp_admin@mycorp.com",
                "password": "Password123!",
                "full_name": "Corp Admin",
                "company_name": "MyCorp",
            },
        )
        assert resp_a.status_code == 201
        token_a = resp_a.json()["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # Admin A creates Database connection
        db_resp = client.post(
            "/api/v1/connections",
            headers=headers_a,
            json={
                "name": "MyCorp Production",
                "connection_type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "database_name": "prod",
                "username": "admin",
                "password": "secretpassword",
            },
        )
        assert db_resp.status_code == 201

        # Admin A creates User A2 (Analyst)
        create_user_resp = client.post(
            "/api/v1/users",
            headers=headers_a,
            json={
                "email": "analyst@mycorp.com",
                "password": "Password123!",
                "full_name": "MyCorp Analyst",
                "role": "Analyst",
            },
        )
        assert create_user_resp.status_code == 201
        analyst_user = create_user_resp.json()
        assert analyst_user["company_name"] == "MyCorp"

        # Login as User A2
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@mycorp.com", "password": "Password123!"},
        )
        assert login_resp.status_code == 200
        token_analyst = login_resp.json()["access_token"]
        headers_analyst = {"Authorization": f"Bearer {token_analyst}"}

        # Analyst A2 lists databases -> CAN see MyCorp's database
        list_resp = client.get("/api/v1/connections", headers=headers_analyst)
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1
        assert list_resp.json()["connections"][0]["name"] == "MyCorp Production"

    def test_role_user_count_scoped_to_company(self, client, db_session, permissions):
        # Create SuperAdmin system role
        _make_role(db_session, "SuperAdmin", list(permissions.values()), is_system=True)

        # 1. Register Admin A (Company A)
        resp_a = client.post(
            "/api/v1/auth/register",
            json={
                "email": "admin_company_a@test.com",
                "password": "Password123!",
                "full_name": "Company A Admin",
                "company_name": "Company A",
            },
        )
        assert resp_a.status_code == 201
        token_a = resp_a.json()["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # 2. Register Admin B (Company B)
        resp_b = client.post(
            "/api/v1/auth/register",
            json={
                "email": "admin_company_b@test.com",
                "password": "Password123!",
                "full_name": "Company B Admin",
                "company_name": "Company B",
            },
        )
        assert resp_b.status_code == 201
        token_b = resp_b.json()["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Admin A lists roles -> SuperAdmin count should be 1 (only Company A's user)
        roles_resp_a = client.get("/api/v1/roles", headers=headers_a)
        assert roles_resp_a.status_code == 200
        superadmin_role_a = next(r for r in roles_resp_a.json()["roles"] if r["name"] == "SuperAdmin")
        assert superadmin_role_a["user_count"] == 1

        # Admin B lists roles -> SuperAdmin count should be 1 (only Company B's user)
        roles_resp_b = client.get("/api/v1/roles", headers=headers_b)
        assert roles_resp_b.status_code == 200
        superadmin_role_b = next(r for r in roles_resp_b.json()["roles"] if r["name"] == "SuperAdmin")
        assert superadmin_role_b["user_count"] == 1

    def test_audit_logs_scoped_to_company(self, client, db_session, permissions):
        # Create SuperAdmin system role
        _make_role(db_session, "SuperAdmin", list(permissions.values()), is_system=True)

        # 1. Register Admin A (Company A)
        resp_a = client.post(
            "/api/v1/auth/register",
            json={
                "email": "audit_admin_a@comp-a.com",
                "password": "Password123!",
                "full_name": "Audit Admin A",
                "company_name": "Audit Company A",
            },
        )
        assert resp_a.status_code == 201
        token_a = resp_a.json()["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # 2. Register Admin B (Company B)
        resp_b = client.post(
            "/api/v1/auth/register",
            json={
                "email": "audit_admin_b@comp-b.com",
                "password": "Password123!",
                "full_name": "Audit Admin B",
                "company_name": "Audit Company B",
            },
        )
        assert resp_b.status_code == 201
        token_b = resp_b.json()["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # Admin A checks audit logs -> should only see Company A's events
        audit_resp_a = client.get("/api/v1/audit/logs", headers=headers_a)
        assert audit_resp_a.status_code == 200
        logs_a = audit_resp_a.json()["logs"]
        assert len(logs_a) >= 1
        assert all(log["user_email"] != "audit_admin_b@comp-b.com" for log in logs_a)
        assert any(log["user_email"] == "audit_admin_a@comp-a.com" for log in logs_a)

        # Admin B checks audit logs -> should only see Company B's events
        audit_resp_b = client.get("/api/v1/audit/logs", headers=headers_b)
        assert audit_resp_b.status_code == 200
        logs_b = audit_resp_b.json()["logs"]
        assert len(logs_b) >= 1
        assert all(log["user_email"] != "audit_admin_a@comp-a.com" for log in logs_b)
        assert any(log["user_email"] == "audit_admin_b@comp-b.com" for log in logs_b)

    def test_auto_generated_dashboard_visible_to_all_roles_in_company(self, client, db_session, permissions):
        # Create SuperAdmin, Analyst, and Viewer system roles
        superadmin_role = _make_role(db_session, "SuperAdmin", list(permissions.values()), is_system=True)
        analyst_role = _make_role(db_session, "Analyst", [
            permissions["database.read"],
            permissions["query.read"],
            permissions["query.execute"],
            permissions["dashboard.read"],
            permissions["dashboard.create"],
        ], is_system=True)
        viewer_role = _make_role(db_session, "Viewer", [
            permissions["database.read"],
            permissions["dashboard.read"],
        ], is_system=True)

        # 1. Company A & Company B
        comp_a = Company(name="Auto Corp A")
        comp_b = Company(name="Auto Corp B")
        db_session.add_all([comp_a, comp_b])
        db_session.commit()

        # SuperAdmin in Company A
        superadmin_a = User(email="super_a@autocorp.com", password_hash="hash", full_name="Super A", company_id=comp_a.id, is_active=True)
        # Viewer in Company A
        viewer_a = User(email="viewer_a@autocorp.com", password_hash="hash", full_name="Viewer A", company_id=comp_a.id, is_active=True)
        # Viewer in Company B
        viewer_b = User(email="viewer_b@othercorp.com", password_hash="hash", full_name="Viewer B", company_id=comp_b.id, is_active=True)

        db_session.add_all([superadmin_a, viewer_a, viewer_b])
        db_session.commit()

        db_session.add(UserRole(user_id=superadmin_a.id, role_id=superadmin_role.id))
        db_session.add(UserRole(user_id=viewer_a.id, role_id=viewer_role.id))
        db_session.add(UserRole(user_id=viewer_b.id, role_id=viewer_role.id))
        db_session.commit()

        # Database connection in Company A
        conn_a = DatabaseConnection(
            name="Auto DB", connection_type="postgresql", host="localhost",
            port=5432, database_name="autodb", username="user",
            password="pass", created_by=superadmin_a.id, company_id=comp_a.id,
        )
        db_session.add(conn_a)
        db_session.commit()

        # SuperAdmin A auto-generates a dashboard
        from app.services.dashboard_service import auto_generate_from_query
        dash = auto_generate_from_query(
            db_session,
            database_id=conn_a.id,
            query_text="Sales overview",
            user_id=superadmin_a.id,
            company_id=superadmin_a.company_id,
        )

        assert dash.company_id == comp_a.id
        assert dash.auto_generated is True

        # Viewer A in Company A lists dashboards -> MUST see the auto-generated dashboard
        headers_viewer_a = auth_headers(viewer_a)
        list_resp_a = client.get("/api/v1/dashboards", headers=headers_viewer_a)
        assert list_resp_a.status_code == 200
        dashboards_a = list_resp_a.json()["dashboards"]
        assert any(d["id"] == dash.id for d in dashboards_a)

        # Viewer A gets dashboard detail
        get_resp_a = client.get(f"/api/v1/dashboards/{dash.id}", headers=headers_viewer_a)
        assert get_resp_a.status_code == 200
        assert get_resp_a.json()["id"] == dash.id

        # Viewer B in Company B lists dashboards -> MUST NOT see Company A's auto-generated dashboard
        headers_viewer_b = auth_headers(viewer_b)
        list_resp_b = client.get("/api/v1/dashboards", headers=headers_viewer_b)
        assert list_resp_b.status_code == 200
        dashboards_b = list_resp_b.json()["dashboards"]
        assert not any(d["id"] == dash.id for d in dashboards_b)

