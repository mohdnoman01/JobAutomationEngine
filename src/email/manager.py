from __future__ import annotations

from pathlib import Path

from src.email.approval import approve_email, reject_email
from src.email.drafts import load_drafts, save_drafts
from src.email.models import EmailStatus, OutreachEmail
from src.email.sender import EmailSender


class EmailDraftManager:
    def __init__(
        self,
        path: str | Path = "data/output/email_drafts.json",
        sender: EmailSender | None = None,
    ) -> None:
        self.path = Path(path)
        self.sender = sender

    def list(self) -> list[OutreachEmail]:
        return load_drafts(self.path)

    def add(self, email: OutreachEmail) -> OutreachEmail:
        drafts = self.list()

        if any(
            draft.recipient == email.recipient
            and draft.job_url == email.job_url
            for draft in drafts
        ):
            raise ValueError(
                "An email draft already exists for this recipient and job."
            )

        drafts.append(email)
        save_drafts(drafts, self.path)

        return email

    def approve(self, recipient: str) -> OutreachEmail:
        drafts = self.list()
        email = self._find(drafts, recipient)

        approve_email(email)
        save_drafts(drafts, self.path)

        return email

    def reject(self, recipient: str) -> OutreachEmail:
        drafts = self.list()
        email = self._find(drafts, recipient)

        reject_email(email)
        save_drafts(drafts, self.path)

        return email

    def send(self, recipient: str) -> OutreachEmail:
        if self.sender is None:
            raise ValueError("An email sender is required.")

        drafts = self.list()
        email = self._find(drafts, recipient)

        if email.status != EmailStatus.approved:
            raise ValueError("Only approved emails can be sent.")

        self.sender.send(email)
        save_drafts(drafts, self.path)

        return email

    def _find(
        self,
        drafts: list[OutreachEmail],
        recipient: str,
    ) -> OutreachEmail:
        for email in drafts:
            if email.recipient == recipient:
                return email

        raise ValueError(
            f"Email draft not found for recipient: {recipient}"
        )