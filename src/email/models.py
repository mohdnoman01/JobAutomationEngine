from enum import StrEnum

from pydantic import BaseModel


class EmailStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"
    sent = "sent"
    failed = "failed"


class OutreachEmail(BaseModel):
    recipient: str
    subject: str
    body: str
    company: str
    job_url: str | None = None
    contact_name: str | None = None
    status: EmailStatus = EmailStatus.draft
    