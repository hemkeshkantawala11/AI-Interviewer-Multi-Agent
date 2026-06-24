"""Email delivery tool.

Uses SMTP if configured; otherwise writes the report to ./reports and logs a
'sent' confirmation to the console. This keeps the high-impact 'send to student'
action real, while remaining demo-safe offline.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from ..config import REPORTS_DIR, settings

log = logging.getLogger("interviewer.email")


def send_email(to: str, subject: str, html_body: str, tag: str = "report") -> dict:
    # Always persist a copy for the demo / audit trail.
    safe = "".join(c for c in tag if c.isalnum() or c in "-_")
    path: Path = REPORTS_DIR / f"{safe}.html"
    path.write_text(html_body, encoding="utf-8")

    if not (settings.smtp_host and settings.smtp_user):
        log.info("[EMAIL:console] to=%s subject=%r (saved to %s)", to, subject, path)
        print(f"\n[EMAIL SIMULATED] To: {to} | Subject: {subject}\n  -> saved: {path}")
        return {"delivered": False, "simulated": True, "path": str(path), "to": to}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
        s.starttls()
        s.login(settings.smtp_user, settings.smtp_password)
        s.sendmail(settings.smtp_user, [to], msg.as_string())
    log.info("[EMAIL:smtp] sent to %s", to)
    return {"delivered": True, "simulated": False, "path": str(path), "to": to}
