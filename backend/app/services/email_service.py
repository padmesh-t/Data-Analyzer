import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import settings

logger = logging.getLogger(__name__)


def send_reset_email(to_email: str, token: str) -> None:
    host = settings.SMTP_HOST
    if not host:
        link = f"{settings.APP_URL}/reset-password?token={token}"
        logger.warning("SMTP not configured — reset link for %s: %s", to_email, link)
        return

    reset_link = f"{settings.APP_URL}/reset-password?token={token}"

    subject = "Password Reset — Agentic Analytics"
    body_html = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; padding: 24px; color: #333;">
  <h2>Password Reset Request</h2>
  <p>We received a request to reset your password for <strong>Agentic Analytics</strong>.</p>
  <p>Click the button below to set a new password. This link expires in 30 minutes.</p>
  <p style="text-align: center; margin: 32px 0;">
    <a href="{reset_link}"
       style="background-color: #6366f1; color: #fff; padding: 12px 32px;
              border-radius: 6px; text-decoration: none; display: inline-block;">
      Reset Password
    </a>
  </p>
  <p>If you didn't request this, you can safely ignore this email.</p>
  <hr style="border: none; border-top: 1px solid #e5e7eb;" />
  <p style="font-size: 12px; color: #9ca3af;">Agentic Analytics &mdash; {settings.APP_URL}</p>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(host, settings.SMTP_PORT, timeout=10) as server:
            if settings.SMTP_PORT == 587:
                server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
            logger.info("Reset email sent to %s", to_email)
    except Exception as e:
        logger.error("Failed to send reset email to %s: %s", to_email, e)
        link = f"{settings.APP_URL}/reset-password?token={token}"
        logger.warning("Fallback — reset link for %s: %s", to_email, link)
