from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, field_validator


class ApplicationStatus(str, Enum):
    discovered = "discovered"
    ready = "ready"
    applied = "applied"
    rejected = "rejected"
    interview = "interview"
    offer = "offer"
    withdrawn = "withdrawn"


class Application(BaseModel):
    company: str
    job_title: str
    job_url: str
    status: ApplicationStatus = ApplicationStatus.discovered
    contact_name: str | None = None
    contact_email: str | None = None
    applied_date: date | None = None
    notes: str | None = None

    @field_validator("company", "job_title", "job_url")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("contact_email", "contact_name", "notes")
    @classmethod
    def strip_optional_strings(cls, value: str | None) -> str | None:
        return value.strip() if value else value