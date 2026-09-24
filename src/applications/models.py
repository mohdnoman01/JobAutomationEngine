from __future__ import annotations

from datetime import date
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, field_validator


class ApplicationStatus(str, Enum):
    discovered = "discovered"
    ready = "ready"
    applied = "applied"
    rejected = "rejected"
    interview = "interview"
    offer = "offer"
    withdrawn = "withdrawn"


VALID_APPLICATION_TRANSITIONS: dict[
    ApplicationStatus, frozenset[ApplicationStatus]
] = {
    ApplicationStatus.discovered: frozenset(
        {ApplicationStatus.ready, ApplicationStatus.rejected}
    ),
    ApplicationStatus.ready: frozenset(
        {
            ApplicationStatus.applied,
            ApplicationStatus.rejected,
            ApplicationStatus.withdrawn,
        }
    ),
    ApplicationStatus.applied: frozenset(
        {
            ApplicationStatus.interview,
            ApplicationStatus.rejected,
            ApplicationStatus.withdrawn,
        }
    ),
    ApplicationStatus.interview: frozenset(
        {
            ApplicationStatus.offer,
            ApplicationStatus.rejected,
            ApplicationStatus.withdrawn,
        }
    ),
    ApplicationStatus.offer: frozenset(),
    ApplicationStatus.rejected: frozenset(),
    ApplicationStatus.withdrawn: frozenset(),
}


class Application(BaseModel):
    _transitions: ClassVar[
        dict[ApplicationStatus, frozenset[ApplicationStatus]]
    ] = VALID_APPLICATION_TRANSITIONS

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

    def transition_to(self, status: ApplicationStatus) -> None:
        allowed_statuses = self._transitions[self.status]

        if status not in allowed_statuses:
            raise ValueError(
                f"Invalid application status transition: "
                f"{self.status.value} -> {status.value}"
            )

        self.status = status