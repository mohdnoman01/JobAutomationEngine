from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from src.email.models import EmailStatus, OutreachEmail
from src.email.sender import EmailSender


class GmailEmailSender(EmailSender):
    def __init__(self, service: Any) -> None:
        self.service = service

    def send(self, email: OutreachEmail) -> OutreachEmail:
        if email.status != EmailStatus.approved:
            raise ValueError("Only approved emails can be sent.")

        message = EmailMessage()
        message["To"] = email.recipient
        message["Subject"] = email.subject
        message.set_content(email.body)

        encoded_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode()

        self.service.users().messages().send(
            userId="me",
            body={"raw": encoded_message},
        ).execute()

        email.status = EmailStatus.sent
        return email