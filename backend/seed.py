"""Seed script to create initial roles, permissions, and admin user."""
from sqlalchemy import inspect, text

from app.database import SessionLocal, engine, Base
from app.models.user import User, UserRole
from app.models.role import Role, RolePermission
from app.models.permission import Permission
from app.utils.security import get_password_hash


def _ensure_columns():
    """Add columns that may be missing on existing tables."""
    inspector = inspect(engine)
    existing = {c["name"] for c in inspector.get_columns("users")}
    with engine.connect() as conn:
        if "reset_token" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN reset_token VARCHAR(255)"))
        if "reset_token_expires" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP WITH TIME ZONE"))
        conn.commit()


def seed():
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    db = SessionLocal()

    try:
        # Create permissions
        permissions_data = [
            # Database permissions
            ("database.create", "database", "create", "Create new database connections"),
            ("database.read", "database", "read", "View database connections"),
            ("database.update", "database", "update", "Update database connections"),
            ("database.delete", "database", "delete", "Delete database connections"),
            ("database.test", "database", "test", "Test database connections"),
            ("database.sync", "database", "sync", "Sync database schemas"),
            ("database.schema", "database", "schema", "View database schemas"),
            ("database.connect", "database", "connect", "Connect to databases"),
            # Query permissions
            ("query.execute", "query", "execute", "Execute SQL queries"),
            ("query.read", "query", "read", "View query history"),
            ("query.delete", "query", "delete", "Delete queries"),
            # Dashboard permissions
            ("dashboard.create", "dashboard", "create", "Create dashboards"),
            ("dashboard.read", "dashboard", "read", "View dashboards"),
            ("dashboard.update", "dashboard", "update", "Update dashboards"),
            ("dashboard.delete", "dashboard", "delete", "Delete dashboards"),
            # User management
            ("user.create", "user", "create", "Create users"),
            ("user.read", "user", "read", "View users"),
            ("user.update", "user", "update", "Update users"),
            ("user.delete", "user", "delete", "Delete users"),
            # Role management
            ("role.create", "role", "create", "Create roles"),
            ("role.read", "role", "read", "View roles"),
            ("role.update", "role", "update", "Update roles"),
            ("role.delete", "role", "delete", "Delete roles"),
            # Access management
            ("access.manage", "access", "manage", "Manage user access"),
            # Data import
            ("import.csv", "import", "csv", "Import CSV files"),
            ("import.history", "import", "history", "View import history"),
            # Export
            ("export.pdf", "export", "pdf", "Export to PDF"),
            ("export.excel", "export", "excel", "Export to Excel"),
            ("export.png", "export", "png", "Export to PNG"),
            # Audit
            ("audit.read", "audit", "read", "View audit logs"),
        ]

        for name, resource, action, desc in permissions_data:
            existing = db.query(Permission).filter(Permission.name == name).first()
            if not existing:
                db.add(Permission(name=name, resource=resource, action=action, description=desc))

        db.commit()

        # Create roles
        roles_data = {
            "SuperAdmin": {
                "description": "Full system access with all permissions",
                "is_system": True,
            },
            "Admin": {
                "description": "Administrative access with user and role management",
                "is_system": True,
            },
            "Analyst": {
                "description": "Can query data and create dashboards",
                "is_system": True,
            },
            "Viewer": {
                "description": "Can view dashboards and reports",
                "is_system": True,
            },
        }

        all_permissions = {p.name: p.id for p in db.query(Permission).all()}

        for role_name, role_info in roles_data.items():
            existing_role = db.query(Role).filter(Role.name == role_name).first()
            if existing_role:
                continue

            role = Role(
                name=role_name,
                description=role_info["description"],
                is_system=role_info["is_system"],
            )
            db.add(role)
            db.commit()
            db.refresh(role)

            # Assign permissions based on role
            if role_name == "SuperAdmin":
                perm_ids = list(all_permissions.values())
            elif role_name == "Admin":
                admin_perms = [p for name, p in all_permissions.items()
                              if not name.startswith("superadmin")]
                perm_ids = admin_perms
            elif role_name == "Analyst":
                analyst_perms = [p for name, p in all_permissions.items()
                                if name.startswith(("database.read", "query.", "dashboard."))]
                perm_ids = analyst_perms
            elif role_name == "Viewer":
                viewer_perms = [p for name, p in all_permissions.items()
                               if name in ("database.read", "query.read", "dashboard.read")]
                perm_ids = viewer_perms
            else:
                perm_ids = []

            for pid in perm_ids:
                db.add(RolePermission(role_id=role.id, permission_id=pid))

        db.commit()

        # Create admin user if not exists
        admin_email = "admin@agentic.com"
        existing_admin = db.query(User).filter(User.email == admin_email).first()
        if not existing_admin:
            admin = User(
                email=admin_email,
                password_hash=get_password_hash("admin123"),
                full_name="System Admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)

            # Assign SuperAdmin role
            superadmin_role = db.query(Role).filter(Role.name == "SuperAdmin").first()
            if superadmin_role:
                db.add(UserRole(user_id=admin.id, role_id=superadmin_role.id))
                db.commit()

        print("Seed completed successfully!")
        print(f"Admin login: {admin_email} / admin123")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
