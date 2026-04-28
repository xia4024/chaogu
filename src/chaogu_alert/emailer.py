from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib

from .config import EmailSettings


def send_email(
    settings: EmailSettings, subject: str, text_body: str, html_body: str | None = None
) -> None:
    if not settings.enabled:
        raise ValueError("Email is disabled in config.")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = settings.from_addr
    message["To"] = ", ".join(settings.to_addrs)
    message.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        message.attach(MIMEText(html_body, "html", "utf-8"))

    if settings.use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
            server.login(settings.username, settings.password)
            server.sendmail(settings.from_addr, settings.to_addrs, message.as_string())
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.username, settings.password)
        server.sendmail(settings.from_addr, settings.to_addrs, message.as_string())
