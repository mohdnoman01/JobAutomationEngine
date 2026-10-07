from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class AttemptOutcome(StrEnum):
    started = "started"
    needs_review = "needs_review"
    submitting = "submitting"
    submitted = "submitted"
    failed = "failed"
    unsupported = "unsupported"
    human_action_required = "human_action_required"
    unknown_submission_result = "unknown_submission_result"


class SubmissionOutcome(StrEnum):
    not_started = "not_started"
    not_submitted = "not_submitted"
    submitted = "submitted"
    unknown = "unknown"


class FailureCategory(StrEnum):
    review_required = "review_required"
    unknown_question = "unknown_question"
    missing_profile_data = "missing_profile_data"
    unsupported_platform = "unsupported_platform"
    unsupported_capability = "unsupported_capability"
    human_action = "human_action"
    preparation_error = "preparation_error"
    adapter_error = "adapter_error"
    submit_error = "submit_error"
    verification_error = "verification_error"


class ReviewQuestionSnapshot(BaseModel):
    key: str
    label: str
    required: bool
    question_type: str


class ApplicationAttempt(BaseModel):
    application_id: str
    company: str
    job_title: str
    job_url: str
    platform: str
    attempt_number: int
    started_at: datetime
    completed_at: datetime | None = None
    current_step: str = "start"
    last_successful_step: str | None = None
    outcome: AttemptOutcome = AttemptOutcome.started
    failure_category: FailureCategory | None = None
    error_message: str | None = None
    retryable: bool = False
    submission_outcome: SubmissionOutcome = SubmissionOutcome.not_started
    verification_result: bool | None = None
    verification_evidence: str | None = None
    reconciliation_evidence: str | None = None
    review_questions: list[ReviewQuestionSnapshot] = Field(default_factory=list)
    missing_profile_fields: list[str] = Field(default_factory=list)


class AttemptStore:
    def __init__(self, path: str | Path = "data/output/application_attempts.json") -> None:
        self.path = Path(path)

    def list(self) -> list[ApplicationAttempt]:
        if not self.path.exists():
            return []
        raw_attempts = json.loads(self.path.read_text(encoding="utf-8"))
        return [ApplicationAttempt.model_validate(item) for item in raw_attempts]

    def latest_for(self, application_id: str) -> ApplicationAttempt | None:
        matching = [
            attempt
            for attempt in self.list()
            if attempt.application_id == application_id
        ]
        return max(matching, key=lambda attempt: attempt.attempt_number, default=None)

    def start(
        self,
        *,
        application_id: str,
        company: str,
        job_title: str,
        job_url: str,
        platform: str,
    ) -> ApplicationAttempt:
        attempts = self.list()
        attempt = ApplicationAttempt(
            application_id=application_id,
            company=company,
            job_title=job_title,
            job_url=job_url,
            platform=platform,
            attempt_number=1 + max(
                (
                    item.attempt_number
                    for item in attempts
                    if item.application_id == application_id
                ),
                default=0,
            ),
            started_at=datetime.now(timezone.utc),
        )
        attempts.append(attempt)
        self._save(attempts)
        return attempt

    def update(self, attempt: ApplicationAttempt, **changes: object) -> ApplicationAttempt:
        attempts = self.list()
        changes = {key: value for key, value in changes.items() if value is not None}
        for index, existing in enumerate(attempts):
            if (
                existing.application_id == attempt.application_id
                and existing.attempt_number == attempt.attempt_number
            ):
                updated = existing.model_copy(update=changes)
                attempts[index] = updated
                self._save(attempts)
                return updated
        raise ValueError("Application attempt not found")

    def _save(self, attempts: list[ApplicationAttempt]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                [attempt.model_dump(mode="json") for attempt in attempts],
                indent=2,
            ),
            encoding="utf-8",
        )


class RecoveryQueue:
    RECOVERY_OUTCOMES = frozenset(
        {
            AttemptOutcome.needs_review,
            AttemptOutcome.failed,
            AttemptOutcome.unsupported,
            AttemptOutcome.human_action_required,
            AttemptOutcome.unknown_submission_result,
        }
    )

    def __init__(self, attempts: AttemptStore) -> None:
        self.attempts = attempts

    def list(self) -> list[ApplicationAttempt]:
        return [
            attempt
            for attempt in self.attempts.list()
            if attempt.outcome in self.RECOVERY_OUTCOMES
        ]

    def retryable(self) -> list[ApplicationAttempt]:
        return [attempt for attempt in self.list() if attempt.retryable]
