from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import html
import smtplib
from typing import Iterable


@dataclass(frozen=True)
class EmailSettings:
    sender_email: str
    recipient_email: str
    gmail_app_password: str


def normalize_recipients(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        candidates = value.replace(";", ",").split(",")
    else:
        candidates = list(value)
    recipients: list[str] = []
    for candidate in candidates:
        address = str(candidate).strip()
        if address and address not in recipients:
            recipients.append(address)
    if not recipients:
        raise RuntimeError("Email收件者清單不可空白。")
    return recipients


def build_email_message(settings: EmailSettings, subject: str, plain_text: str, html_body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender_email
    message["To"] = settings.recipient_email
    message.set_content(plain_text)
    message.add_alternative(html_body, subtype="html")
    return message


def send_gmail(settings: EmailSettings, subject: str, plain_text: str, html_body: str) -> None:
    recipients = normalize_recipients(settings.recipient_email)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(settings.sender_email, settings.gmail_app_password)
        # 每位收件者各寄一封，避免暴露其他訂閱者地址。
        for recipient in recipients:
            recipient_settings = EmailSettings(
                settings.sender_email,
                recipient,
                settings.gmail_app_password,
            )
            smtp.send_message(build_email_message(recipient_settings, subject, plain_text, html_body))


def simple_html(title: str, paragraphs: list[str]) -> str:
    body = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
    return (
        "<!doctype html><html><body style='font-family:Arial,sans-serif;color:#17302f;"
        "max-width:720px;margin:24px auto;line-height:1.6'>"
        f"<h2>{html.escape(title)}</h2>{body}</body></html>"
    )
