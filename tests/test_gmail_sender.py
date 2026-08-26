from unittest.mock import Mock

import pytest

from src.email.gmail import GmailEmailSender
from src.email.models import EmailStatus, OutreachEmail


def make_email(status: EmailStatus) -> OutreachEmail:
    return OutreachEmail(
        recipient="recruiter@example.com",
        subject="Android Developer",
        body="Hello Recruiter",
        company="Test Startup",
        status=status,
    )


def test_gmail_sender_sends_approved_email():
    service = Mock()

    service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "test-message-id"
    }

    email = make_email(EmailStatus.approved)

    result = GmailEmailSender(service).send(email)

    assert result.status == EmailStatus.sent

    service.users.return_value.messages.return_value.send.assert_called_once()


def test_gmail_sender_rejects_draft():
    service = Mock()
    email = make_email(EmailStatus.draft)

    with pytest.raises(ValueError):
        GmailEmailSender(service).send(email)

    service.users.return_value.messages.return_value.send.assert_not_called()


def test_gmail_sender_rejects_rejected_email():
    service = Mock()
    email = make_email(EmailStatus.rejected)

    with pytest.raises(ValueError):
        GmailEmailSender(service).send(email)

    service.users.return_value.messages.return_value.send.assert_not_called()


def test_gmail_sender_rejects_already_sent_email():
    service = Mock()
    email = make_email(EmailStatus.sent)

    with pytest.raises(ValueError):
        GmailEmailSender(service).send(email)

    service.users.return_value.messages.return_value.send.assert_not_called()