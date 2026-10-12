from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field
from src.applications.evidence import (
    BrowserObservation,
    EvidenceObserver,
    VerifiedSubmissionEvidence,
)
from src.research.job_normalizer import normalize_job_url

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    msvcrt = None
try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None


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
    payload_fingerprint: str | None = None
    browser_action_started: bool = False
    browser_action_id: str | None = None
    browser_observation: BrowserObservation | None = None
    verified_evidence_record: VerifiedSubmissionEvidence | None = None
    verified_evidence_consumed: bool = False
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
    def __init__(
        self,
        path: str | Path = "data/output/application_attempts.json",
        *,
        allow_test_fixture_evidence: bool = False,
    ) -> None:
        self.path = Path(path)
        self.allow_test_fixture_evidence = allow_test_fixture_evidence

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

    def has_submission_in_progress(self, application_id: str) -> bool:
        return any(
            attempt.application_id == application_id
            and attempt.outcome == AttemptOutcome.submitting
            for attempt in self.list()
        )

    def matches_submission_claim(
        self,
        *,
        application_id: str,
        job_url: str,
        attempt_number: int,
        payload_fingerprint: str,
        allow_completed_verified: bool = False,
    ) -> bool:
        """Check the exact live persisted claim required by a browser submit."""
        with self._locked():
            return any(
                item.application_id == application_id
                and normalize_job_url(item.job_url) == normalize_job_url(job_url)
                and item.attempt_number == attempt_number
                and (
                    item.outcome == AttemptOutcome.submitting
                    or (
                        allow_completed_verified
                        and item.outcome == AttemptOutcome.submitted
                        and item.verification_result is True
                        and bool(item.verification_evidence)
                    )
                )
                and item.payload_fingerprint == payload_fingerprint
                for item in self.list()
            )

    def begin_browser_submission_action(
        self,
        *,
        application_id: str,
        job_url: str,
        attempt_number: int,
        payload_fingerprint: str,
    ) -> str | None:
        """Atomically consume the active claim before the irreversible browser action."""
        with self._locked():
            attempts = self.list()
            for index, item in enumerate(attempts):
                if (
                    item.application_id != application_id
                    or normalize_job_url(item.job_url) != normalize_job_url(job_url)
                    or item.attempt_number != attempt_number
                    or item.payload_fingerprint != payload_fingerprint
                    or item.outcome != AttemptOutcome.submitting
                    or item.browser_action_started
                ):
                    continue
                browser_action_id = str(uuid4())
                attempts[index] = item.model_copy(
                    update={
                        "browser_action_started": True,
                        "browser_action_id": browser_action_id,
                    }
                )
                self._save(attempts)
                return browser_action_id
            return None

    def record_browser_observation(self, observation: BrowserObservation) -> bool:
        """Persist the first observation belonging to a consumed browser action."""
        if (
            observation.observer == EvidenceObserver.test_fixture
            and not self.allow_test_fixture_evidence
        ):
            return False
        with self._locked():
            attempts = self.list()
            for index, item in enumerate(attempts):
                if (
                    item.application_id != observation.application_id
                    or normalize_job_url(item.job_url)
                    != normalize_job_url(observation.job_url)
                    or item.attempt_number != observation.attempt_number
                    or item.payload_fingerprint != observation.payload_fingerprint
                    or item.outcome != AttemptOutcome.submitting
                    or not item.browser_action_started
                    or item.browser_action_id != observation.browser_action_id
                    or item.browser_observation is not None
                    or item.verified_evidence_record is not None
                ):
                    continue
                attempts[index] = item.model_copy(
                    update={"browser_observation": observation}
                )
                self._save(attempts)
                return True
            return False

    def record_verified_evidence(
        self,
        attempt: ApplicationAttempt,
        evidence: VerifiedSubmissionEvidence,
    ) -> bool:
        """Persist adapter verification only when it matches the stored observation."""
        with self._locked():
            attempts = self.list()
            for index, item in enumerate(attempts):
                if (
                    item.application_id != attempt.application_id
                    or item.attempt_number != attempt.attempt_number
                    or normalize_job_url(item.job_url) != normalize_job_url(attempt.job_url)
                    or item.outcome != AttemptOutcome.submitting
                    or not item.browser_action_started
                    or item.verified_evidence_consumed
                    or item.verified_evidence_record is not None
                    or item.browser_observation is None
                    or evidence.observation != item.browser_observation
                    or (
                        item.browser_observation.observer
                        == EvidenceObserver.test_fixture
                        and not self.allow_test_fixture_evidence
                    )
                    or not evidence.validates_for(
                        application_id=item.application_id,
                        job_url=item.job_url,
                        attempt_number=item.attempt_number,
                        payload_fingerprint=item.payload_fingerprint or "",
                    )
                ):
                    continue
                attempts[index] = item.model_copy(
                    update={"verified_evidence_record": evidence}
                )
                self._save(attempts)
                return True
            return False

    def consume_verified_evidence(
        self,
        attempt: ApplicationAttempt,
        evidence: VerifiedSubmissionEvidence,
    ) -> bool:
        """Atomically consume exact persisted evidence for one completion."""
        with self._locked():
            attempts = self.list()
            for index, item in enumerate(attempts):
                if (
                    item.application_id != attempt.application_id
                    or normalize_job_url(item.job_url) != normalize_job_url(attempt.job_url)
                    or item.attempt_number != attempt.attempt_number
                    or item.payload_fingerprint != attempt.payload_fingerprint
                    or item.outcome
                    not in {AttemptOutcome.submitting, AttemptOutcome.submitted}
                    or not item.browser_action_started
                    or not item.browser_action_id
                    or item.browser_observation is None
                    or item.verified_evidence_record != evidence
                    or item.verified_evidence_consumed
                    or (
                        item.browser_observation.observer
                        == EvidenceObserver.test_fixture
                        and not self.allow_test_fixture_evidence
                    )
                    or not evidence.validates_for(
                        application_id=item.application_id,
                        job_url=item.job_url,
                        attempt_number=item.attempt_number,
                        payload_fingerprint=item.payload_fingerprint or "",
                    )
                    or (
                        item.outcome == AttemptOutcome.submitted
                        and (
                            item.verification_result is not True
                            or not item.verification_evidence
                        )
                    )
                ):
                    continue
                attempts[index] = item.model_copy(
                    update={"verified_evidence_consumed": True}
                )
                self._save(attempts)
                return True
            return False

    def matches_consumed_verified_evidence(
        self,
        attempt: ApplicationAttempt,
        evidence: VerifiedSubmissionEvidence,
    ) -> bool:
        return self.matches_persisted_verified_evidence(
            attempt, evidence, consumed=True
        )

    def matches_persisted_verified_evidence(
        self,
        attempt: ApplicationAttempt,
        evidence: VerifiedSubmissionEvidence,
        *,
        consumed: bool | None = False,
    ) -> bool:
        """Match evidence persisted for this action, optionally requiring consumption."""
        with self._locked():
            return any(
                item.application_id == attempt.application_id
                and item.attempt_number == attempt.attempt_number
                and normalize_job_url(item.job_url) == normalize_job_url(attempt.job_url)
                and item.payload_fingerprint == attempt.payload_fingerprint
                and item.outcome in {AttemptOutcome.submitting, AttemptOutcome.submitted}
                and item.browser_action_started
                and bool(item.browser_action_id)
                and item.browser_observation is not None
                and (
                    consumed is None
                    or item.verified_evidence_consumed is consumed
                )
                and item.verified_evidence_record == evidence
                and not (
                    item.browser_observation.observer == EvidenceObserver.test_fixture
                    and not self.allow_test_fixture_evidence
                )
                and evidence.validates_for(
                    application_id=item.application_id,
                    job_url=item.job_url,
                    attempt_number=item.attempt_number,
                    payload_fingerprint=item.payload_fingerprint or "",
                )
                and (
                    item.outcome != AttemptOutcome.submitted
                    or (
                        item.verification_result is True
                        and bool(item.verification_evidence)
                    )
                )
                for item in self.list()
            )

    def start(
        self,
        *,
        application_id: str,
        company: str,
        job_title: str,
        job_url: str,
        platform: str,
    ) -> ApplicationAttempt:
        with self._locked():
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

    def claim_submission(
        self,
        *,
        application_id: str,
        company: str,
        job_title: str,
        job_url: str,
        platform: str,
        attempt: ApplicationAttempt | None = None,
        payload_fingerprint: str | None = None,
    ) -> ApplicationAttempt | None:
        """Persist a one-use submit claim under a process-shared file lock.

        Direct adapter claims are never recycled. The engine can claim its
        current attempt after its explicit retry policy has admitted it.
        """
        with self._locked():
            attempts = self.list()
            prior = [item for item in attempts if item.application_id == application_id]
            if attempt is None and prior:
                return None
            if any(
                item.outcome
                in {
                    AttemptOutcome.submitting,
                    AttemptOutcome.submitted,
                    AttemptOutcome.unknown_submission_result,
                }
                for item in prior
            ):
                return None
            if attempt is not None:
                claimed = attempt.model_copy(
                    update={
                        "current_step": "submit",
                        "last_successful_step": "fill",
                        "outcome": AttemptOutcome.submitting,
                        "submission_outcome": SubmissionOutcome.unknown,
                        "retryable": False,
                        "payload_fingerprint": payload_fingerprint or attempt.payload_fingerprint,
                    }
                )
                for index, existing in enumerate(attempts):
                    if (existing.application_id, existing.attempt_number) == (
                        attempt.application_id,
                        attempt.attempt_number,
                    ):
                        attempts[index] = claimed
                        break
                else:
                    return None
            else:
                claimed = ApplicationAttempt(
                    application_id=application_id,
                    company=company,
                    job_title=job_title,
                    job_url=job_url,
                    platform=platform,
                    attempt_number=1 + max((item.attempt_number for item in prior), default=0),
                    started_at=datetime.now(timezone.utc),
                    current_step="submit",
                    outcome=AttemptOutcome.submitting,
                    submission_outcome=SubmissionOutcome.unknown,
                    payload_fingerprint=payload_fingerprint,
                )
                attempts.append(claimed)
            self._save(attempts)
            return claimed

    def update(self, attempt: ApplicationAttempt, **changes: object) -> ApplicationAttempt:
        with self._locked():
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
        content = json.dumps(
            [attempt.model_dump(mode="json") for attempt in attempts], indent=2
        )
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        with os.fdopen(descriptor, "r+b", buffering=0) as lock_file:
            if msvcrt is not None:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover
                raise RuntimeError("No supported file locking implementation")


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
