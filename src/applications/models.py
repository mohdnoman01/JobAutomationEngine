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


class AutomationStatus(str, Enum):
    not_started = "not_started"
    preparing = "preparing"
    needs_review = "needs_review"
    ready_to_submit = "ready_to_submit"
    submitting = "submitting"
    submitted = "submitted"
    failed = "failed"
    unsupported = "unsupported"
    human_action_required = "human_action_required"
    unknown_submission_result = "unknown_submission_result"


VALID_AUTOMATION_TRANSITIONS: dict[
    AutomationStatus, frozenset[AutomationStatus]
] = {
    AutomationStatus.not_started: frozenset(
        {AutomationStatus.preparing, AutomationStatus.unsupported}
    ),
    AutomationStatus.preparing: frozenset(
        {
            AutomationStatus.needs_review,
            AutomationStatus.ready_to_submit,
            AutomationStatus.failed,
            AutomationStatus.unsupported,
            AutomationStatus.human_action_required,
            AutomationStatus.unknown_submission_result,
        }
    ),
    AutomationStatus.needs_review: frozenset(
        {AutomationStatus.preparing, AutomationStatus.human_action_required}
    ),
    AutomationStatus.ready_to_submit: frozenset(
        {
            AutomationStatus.submitting,
            AutomationStatus.failed,
            AutomationStatus.unsupported,
            AutomationStatus.human_action_required,
            AutomationStatus.unknown_submission_result,
        }
    ),
    AutomationStatus.submitting: frozenset(
        {
            AutomationStatus.submitted,
            AutomationStatus.failed,
            AutomationStatus.human_action_required,
            AutomationStatus.unknown_submission_result,
        }
    ),
    AutomationStatus.failed: frozenset({AutomationStatus.preparing}),
    AutomationStatus.unsupported: frozenset({AutomationStatus.preparing}),
    AutomationStatus.human_action_required: frozenset(
        {AutomationStatus.preparing}
    ),
    AutomationStatus.unknown_submission_result: frozenset(),
    AutomationStatus.submitted: frozenset(),
}


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
    automation_status: AutomationStatus = AutomationStatus.not_started
    submission_evidence: str | None = None
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

    def transition_to(
        self,
        status: ApplicationStatus,
        *,
        submission_evidence: str | None = None,
    ) -> None:
        allowed_statuses = self._transitions[self.status]

        if status not in allowed_statuses:
            raise ValueError(
                f"Invalid application status transition: "
                f"{self.status.value} -> {status.value}"
            )

        if status == ApplicationStatus.applied:
            if submission_evidence is None or not submission_evidence.strip():
                raise ValueError(
                    "Submission evidence is required before marking applied"
                )
            self.submission_evidence = submission_evidence.strip()

        self.status = status

    def transition_automation_to(self, status: AutomationStatus) -> None:
        if status not in VALID_AUTOMATION_TRANSITIONS[self.automation_status]:
            raise ValueError(
                f"Invalid automation status transition: "
                f"{self.automation_status.value} -> {status.value}"
            )

        self.automation_status = status