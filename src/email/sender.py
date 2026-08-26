from __future__ import annotations

from abc import ABC, abstractmethod

from src.email.models import EmailStatus, OutreachEmail


class EmailSender(ABC):
    @abstractmethod
    def send(self, email: OutreachEmail) -> OutreachEmail:
        """Send an approved email and return the updated email."""
        raise NotImplementedError


class MockEmailSender(EmailSender):
    def send(self, email: OutreachEmail) -> OutreachEmail:
        if email.status != EmailStatus.approved:
            raise ValueError(
                "Only approved emails can be sent."
            )

        email.status = EmailStatus.sent
        return email  