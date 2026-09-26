"""Security Guard Service for Data-Taker / MCP Server.

Provides a robust multi-layer defense against destructive SQL queries,
prompt injections, jailbreaks, and unauthorized data mutations.
Whenever a violation is detected:
1. Execution is immediately blocked (zero DB mutations permitted).
2. An audit log event is recorded with action 'SECURITY_VIOLATION_BLOCKED'.
3. SuperAdmins are dynamically discovered and notified via high-priority email.
4. A structured alert object is returned for frontend modal display.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.audit import AuditLog
from app.models.user import User, UserRole
from app.models.role import Role
from app.services.email_service import send_security_alert_email

logger = logging.getLogger(__name__)

# Destructive SQL keywords / commands prohibited in read-only environment
PROHIBITED_DML_DDL_PATTERNS = [
    # DDL
    (r"\b(DROP)\s+(TABLE|DATABASE|SCHEMA|VIEW|INDEX|PROCEDURE|FUNCTION|TRIGGER|SEQUENCE|USER|ROLE)\b", "DDL_DROP", "Prohibited DROP statement detected. Schema modifications are not permitted."),
    (r"\b(TRUNCATE)\s+(TABLE\s+)?[a-zA-Z0-9_\.]+", "DDL_TRUNCATE", "Prohibited TRUNCATE statement detected. Data wiping is not permitted."),
    (r"\b(ALTER)\s+(TABLE|DATABASE|SCHEMA|VIEW|INDEX|USER|ROLE)\b", "DDL_ALTER", "Prohibited ALTER statement detected. Schema modifications are not permitted."),
    (r"\b(CREATE)\s+(TABLE|DATABASE|SCHEMA|VIEW|INDEX|PROCEDURE|FUNCTION|TRIGGER|SEQUENCE|USER|ROLE)\b", "DDL_CREATE", "Prohibited CREATE statement detected. Schema modifications are not permitted."),
    (r"\b(RENAME)\s+(TABLE|COLUMN|TO)\b", "DDL_RENAME", "Prohibited RENAME statement detected. Schema modifications are not permitted."),
    
    # Destructive DML / Mutations
    (r"\b(DELETE)\s+FROM\b", "DML_DELETE", "Prohibited DELETE statement detected. Data deletions are strictly blocked."),
    (r"\b(UPDATE)\s+[a-zA-Z0-9_\.]+\s+SET\b", "DML_UPDATE", "Prohibited UPDATE statement detected. Data updates are strictly blocked."),
    (r"\b(INSERT)\s+INTO\b", "DML_INSERT", "Prohibited INSERT statement detected. Data insertions are strictly blocked."),
    (r"\b(MERGE)\s+INTO\b", "DML_MERGE", "Prohibited MERGE statement detected. Data mutations are strictly blocked."),
    (r"\b(UPSERT)\b", "DML_UPSERT", "Prohibited UPSERT statement detected. Data mutations are strictly blocked."),
    (r"\b(REPLACE)\s+INTO\b", "DML_REPLACE", "Prohibited REPLACE statement detected. Data mutations are strictly blocked."),
    
    # Permissions & Administration
    (r"\b(GRANT|REVOKE|DENY)\s+", "PRIVILEGE_MODIFICATION", "Prohibited privilege modification (GRANT/REVOKE/DENY) statement detected."),
    (r"\b(SHUTDOWN|KILL)\b", "ADMIN_COMMAND", "Prohibited administrative command (SHUTDOWN/KILL) detected."),
    (r"\b(ATTACH|DETACH)\s+DATABASE\b", "DB_ATTACH", "Prohibited ATTACH/DETACH command detected."),
    (r"\b(VACUUM|REINDEX|PRAGMA)\b", "ADMIN_UTILITY", "Prohibited administrative utility command detected."),

    # Remote Execution / Shell / File operations
    (r"\b(XP_CMDSHELL|SP_EXECUTESQL|SP_)\b", "SYSTEM_PROCEDURE", "Prohibited system stored procedure detected."),
    (r"\b(EXEC|EXECUTE|CALL)\s+", "EXECUTION_STATEMENT", "Prohibited EXEC/EXECUTE/CALL statement detected."),
    (r"\b(PG_READ_FILE|PG_WRITE_FILE|PG_LS_DIR)\b", "FILE_OPERATION", "Prohibited server file access function detected."),
    (r"\b(LOAD_FILE|INTO\s+OUTFILE|INTO\s+DUMPFILE)\b", "FILE_INJECTION", "Prohibited file import/export injection detected."),
    (r"\b(COPY\s+[a-zA-Z0-9_\.]+\s+(?:FROM|TO))\b", "FILE_COPY", "Prohibited COPY command detected."),
    # Sensitive Credential Exfiltration Protection
    (
        r"(?i)\b(password|password_hash|password_digest|pass_hash|passwd|user_password|user_pass|secret_key|api_key|private_key|auth_token|access_token|refresh_token|client_secret|cvv|credit_card)\b",
        "SENSITIVE_DATA_EXFILTRATION",
        "Prohibited access to sensitive credential / password column detected. Exfiltration of authentication secrets is blocked.",
    ),
]

PROMPT_INJECTION_PATTERNS = [
    (r"(?i)\b(ignore|disregard|forget|override|bypass)\s+(the\s+|all\s+|all\s+the\s+|any\s+)?(previous|prior|above|system|existing)?\s*(instruction|instructions|prompt|prompts|rule|rules|command|commands|constraint|constraints|directive|directives)\b", "PROMPT_INJECTION_OVERRIDE", "Instruction override / prompt injection attempt detected."),
    (r"(?i)\b(system\s+prompt\s+override|developer\s+mode|unrestricted\s+mode|jailbreak|dan\s+mode|god\s+mode|admin\s+mode)\b", "PROMPT_INJECTION_JAILBREAK", "Jailbreak / unrestricted mode override attempt detected."),
    (r"(?i)\b(delete|drop|wipe\s+out|wipe|truncate|erase|purge|destroy)\s+(the\s+|all\s+|all\s+the\s+|every\s+)?(database\s+data|database|databases|db|dbs|data|table|tables|records|rows|users|schema|schemas|everything)\b", "PROMPT_INJECTION_DESTRUCTIVE", "Adversarial destructive instruction targeting database wipeout detected."),
    (r"(?i)\b(delete\s+from|drop\s+table|drop\s+database|drop\s+schema|truncate\s+table|truncate\s+[a-zA-Z0-9_\.]+|update\s+[a-zA-Z0-9_]+\s+set|alter\s+table|insert\s+into)\b", "PROMPT_INJECTION_SQL_SYNTAX", "Direct destructive SQL command embedded in natural language prompt detected."),
    (r"(?i)\b(grant\s+all|give\s+me\s+superadmin|elevate\s+privilege|bypass\s+security|bypass\s+auth|turn\s+off\s+security)\b", "PROMPT_INJECTION_PRIVILEGE", "Privilege elevation / security bypass attempt detected."),
    (r"(?i)\b(xp_cmdshell|exec\s+xp_|execute\s+shell|run\s+bash|run\s+cmd|eval\(|os\.system|subprocess)\b", "PROMPT_INJECTION_RCE", "Remote code/command execution attempt detected in prompt."),
    # Sensitive Credential Exfiltration Protection in NLP Prompts
    (
        r"(?i)\b(show|give|fetch|get|dump|list|reveal|extract|retrieve|select|display|find|export|steal|leak|tell|what\s+is|what\s+are|print|view|see)\b[\s\S]{1,50}\b(password|passwords|password_hash|password_hashes|password_digest|pass_hash|passwd|pwds|pwd|secret_key|secret_keys|auth_token|auth_tokens|api_key|api_keys|private_key|private_keys|client_secret|access_token|refresh_token|credentials?|social_security|ssn|cvv)\b",
        "SENSITIVE_DATA_EXFILTRATION",
        "Prohibited credential / sensitive data access request detected. Exfiltration of authentication secrets is blocked.",
    ),
    (
        r"(?i)\b(password|passwords|password_hash|password_hashes|password_digest|pass_hash|passwd|pwds|pwd|secret_key|secret_keys|auth_token|auth_tokens|api_key|api_keys|private_key|private_keys|client_secret|access_token|refresh_token|credentials?)\b[\s\S]{1,40}\b(of|for|from|in|belonging\s+to|by)\b[\s\S]{1,40}\b(user|users|admin|admins|account|accounts|table|employee|employees|person|people|customer|customers|all|everyone|each|them)\b",
        "SENSITIVE_DATA_EXFILTRATION",
        "Prohibited credential / sensitive data access request detected. Exfiltration of authentication secrets is blocked.",
    ),
    (
        r"(?i)\b(user|users|admin|admins|account|accounts|employee|employees|customer|customers|all|everyone)\s+.*?\b(password|passwords|password_hash|password_hashes|pass_hash|passwd|secret_key|secret_keys|auth_token|auth_tokens|api_key|api_keys|private_key|private_keys|credentials?)\b",
        "SENSITIVE_DATA_EXFILTRATION",
        "Prohibited credential / sensitive data access request detected. Exfiltration of authentication secrets is blocked.",
    ),
]


def strip_sql_comments_and_strings(sql_text: str) -> str:
    """Remove comments and literals from SQL for reliable keyword scanning."""
    if not sql_text:
        return ""
    # Remove block comments /* ... */
    no_block = re.sub(r"/\*[\s\S]*?\*/", " ", sql_text)
    # Remove single line comments -- ... and # ...
    no_single = re.sub(r"(--|#).*?$", " ", no_block, flags=re.MULTILINE)
    # Remove string literals '...' and "..."
    no_strings = re.sub(r"'(?:''|[^'])*'", "''", no_single)
    return no_strings.strip()


def inspect_sql_safety(sql: Optional[str]) -> tuple[bool, Optional[str], Optional[str]]:
    """Inspect a SQL query for destructive operations or stacked injection attacks.
    
    Returns:
        (is_safe, violation_type, reason)
    """
    if not sql or not sql.strip():
        return True, None, None

    cleaned_sql = strip_sql_comments_and_strings(sql)

    # 1. Check for prohibited DML/DDL patterns
    for pattern, v_type, reason in PROHIBITED_DML_DDL_PATTERNS:
        if re.search(pattern, cleaned_sql, flags=re.IGNORECASE):
            logger.warning("Security Guard intercepted SQL violation [%s]: %s | SQL: %s", v_type, reason, sql[:200])
            return False, v_type, reason

    # 2. Check for stacked query injection (multiple statements separated by semicolons)
    # Allow a trailing semicolon at the end of the query
    statements = [s.strip() for s in cleaned_sql.rstrip(";").split(";") if s.strip()]
    if len(statements) > 1:
        # If multiple statements exist, inspect each one individually
        for stmt in statements:
            # Check if any statement starts with or contains prohibited keywords
            for pattern, v_type, reason in PROHIBITED_DML_DDL_PATTERNS:
                if re.search(pattern, stmt, flags=re.IGNORECASE):
                    return False, f"STACKED_{v_type}", f"Stacked statement injection detected: {reason}"
        # Even if multiple SELECT statements, enforce single statement read-only execution
        # Allow multi-statement only if all are read-only SELECT / SHOW / EXPLAIN
        for stmt in statements:
            if not re.match(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|DESC|EXPLAIN)\b", stmt, flags=re.IGNORECASE):
                return False, "STACKED_NON_SELECT", "Stacked query containing non-SELECT statements is prohibited."

    return True, None, None


def inspect_prompt_injection(natural_language: Optional[str]) -> tuple[bool, Optional[str], Optional[str]]:
    """Inspect natural language input for prompt injection, jailbreaks, and destructive instructions.
    
    Returns:
        (is_safe, violation_type, reason)
    """
    if not natural_language or not natural_language.strip():
        return True, None, None

    nl_text = natural_language.strip()

    for pattern, v_type, reason in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, nl_text, flags=re.IGNORECASE):
            logger.warning("Security Guard intercepted Prompt Injection [%s]: %s | Prompt: %s", v_type, reason, nl_text[:200])
            return False, v_type, reason

    return True, None, None


def inspect_query_safety(
    sql: Optional[str] = None,
    natural_language: Optional[str] = None,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Comprehensive check covering both prompt input and SQL statements."""
    if natural_language:
        is_safe, v_type, reason = inspect_prompt_injection(natural_language)
        if not is_safe:
            return False, v_type, reason

    if sql:
        is_safe, v_type, reason = inspect_sql_safety(sql)
        if not is_safe:
            return False, v_type, reason

    return True, None, None


def get_dynamic_superadmins(db: Session, company_id: Optional[int] = None) -> list[User]:
    """Dynamically discover active SuperAdmins and System Admins from the database.
    
    Never relies on hardcoded user emails or user IDs.
    """
    try:
        # Dynamic query: Find users with role matching 'SuperAdmin' or 'Administrator'
        superadmins = (
            db.query(User)
            .join(UserRole, User.id == UserRole.user_id)
            .join(Role, UserRole.role_id == Role.id)
            .filter(
                func.lower(Role.name).in_(["superadmin", "super admin", "systemadmin", "system admin", "administrator"]),
                User.is_active == True,
            )
            .distinct()
            .all()
        )
        if superadmins:
            return superadmins

        # Fallback 1: Any user with role containing 'admin'
        admins = (
            db.query(User)
            .join(UserRole, User.id == UserRole.user_id)
            .join(Role, UserRole.role_id == Role.id)
            .filter(
                func.lower(Role.name).like("%admin%"),
                User.is_active == True,
            )
            .distinct()
            .all()
        )
        if admins:
            return admins

        # Fallback 2: If company_id is provided, check company creator / first active user
        if company_id:
            company_users = db.query(User).filter(User.company_id == company_id, User.is_active == True).all()
            if company_users:
                return company_users

        # Fallback 3: Return any active user in the database
        return db.query(User).filter(User.is_active == True).limit(5).all()
    except Exception as e:
        logger.error("Failed to dynamically lookup SuperAdmins: %s", e)
        return []


def trigger_security_incident(
    db: Session,
    user_id: Optional[int],
    company_id: Optional[int],
    natural_language: Optional[str],
    attempted_sql: Optional[str],
    violation_type: str,
    reason: str,
    source: str = "MCP / Query System",
) -> dict[str, Any]:
    """Handle a detected security violation:
    1. Resolve user info dynamically.
    2. Write AuditLog entry (status='blocked').
    3. Dispatch alert emails to all SuperAdmins.
    4. Return structured alert payload.
    """
    # 1. Resolve User Details dynamically
    user_name = "MCP Agent / Anonymous"
    user_email = "mcp-agent@system.local"
    user_db_id = user_id or 0

    if user_id and user_id > 0:
        try:
            user_obj = db.query(User).filter(User.id == user_id).first()
            if user_obj:
                user_name = user_obj.full_name or user_obj.email
                user_email = user_obj.email
                if company_id is None:
                    company_id = user_obj.company_id
        except Exception as e:
            logger.error("Error retrieving user %s details: %s", user_id, e)

    now_utc = datetime.now(timezone.utc)
    timestamp_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    # 2. Record Audit Log with action 'SECURITY_VIOLATION_BLOCKED'
    try:
        audit_entry = AuditLog(
            user_id=user_db_id,
            company_id=company_id,
            action="SECURITY_VIOLATION_BLOCKED",
            resource_type="query_security_guard",
            resource_id=str(user_db_id),
            status="blocked",
            details={
                "violation_type": violation_type,
                "reason": reason,
                "source": source,
                "natural_language": natural_language,
                "attempted_sql": attempted_sql,
                "user_name": user_name,
                "user_email": user_email,
                "timestamp": timestamp_str,
            },
        )
        db.add(audit_entry)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Failed to write security audit log: %s", e)

    # 3. Discover SuperAdmins dynamically and send emails
    superadmins = get_dynamic_superadmins(db, company_id=company_id)
    notified_count = 0
    for sa in superadmins:
        if sa.email and sa.email != user_email:  # SuperAdmin email (or send even if self in dev)
            sent = send_security_alert_email(
                to_email=sa.email,
                user_name=user_name,
                user_email=user_email,
                user_id=user_db_id,
                company_id=company_id,
                natural_language=natural_language,
                attempted_sql=attempted_sql,
                violation_type=violation_type,
                reason=reason,
                source=source,
                timestamp=timestamp_str,
            )
            if sent:
                notified_count += 1
        elif sa.email:
            # If the user triggering it is the only admin, still notify for visibility
            send_security_alert_email(
                to_email=sa.email,
                user_name=user_name,
                user_email=user_email,
                user_id=user_db_id,
                company_id=company_id,
                natural_language=natural_language,
                attempted_sql=attempted_sql,
                violation_type=violation_type,
                reason=reason,
                source=source,
                timestamp=timestamp_str,
            )
            notified_count += 1

    # 4. Create In-App Notifications for SuperAdmins and User
    try:
        from app.services.notification_service import create_security_violation_notifications
        create_security_violation_notifications(
            db=db,
            offender_user_id=user_id if user_id and user_id > 0 else None,
            offender_name=user_name,
            offender_email=user_email,
            company_id=company_id,
            violation_type=violation_type,
            reason=reason,
            natural_language=natural_language,
            attempted_sql=attempted_sql,
            source=source,
            timestamp=timestamp_str,
        )
    except Exception as e:
        logger.error("Failed to create in-app security notifications: %s", e)

    logger.info("Security incident [%s] processed. Notified %d SuperAdmins via email and in-app alert.", violation_type, notified_count)

    # 5. Construct payload for client/modal
    return {
        "is_violation": True,
        "violation_type": violation_type,
        "reason": reason,
        "attempted_sql": attempted_sql,
        "natural_language": natural_language,
        "timestamp": timestamp_str,
        "user_name": user_name,
        "user_email": user_email,
        "user_id": user_db_id,
        "company_id": company_id,
        "source": source,
        "superadmin_notified": notified_count > 0 or len(superadmins) > 0,
    }
