from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from src.applications.models import Application
from src.research.models import Contact


from datetime import date

from src.applications.models import (
    Application,
    ApplicationStatus,
    AutomationStatus,
)
from src.applications.evidence import VerifiedSubmissionEvidence
from src.applications.identity import application_id_for
from src.applications.persistence import SQLiteSubmissionState, SubmissionCompletion
from src.applications.recovery import (
    ApplicationAttempt,
    AttemptOutcome,
    AttemptStore,
    SubmissionOutcome,
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


def save_applications(
    applications: list[Application],
    path: str | Path = "data/output/applications.json",
) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    data = [application.model_dump(mode="json") for application in applications]

    descriptor, temp_name = tempfile.mkstemp(
        prefix=f"{file_path.name}.", suffix=".tmp", dir=file_path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, file_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_applications(
    path: str | Path = "data/output/applications.json",
) -> list[Application]:
    file_path = Path(path)

    if not file_path.exists():
        return []

    data = json.loads(file_path.read_text(encoding="utf-8"))

    return [Application.model_validate(item) for item in data]


def save_contacts(
    contacts: list[Contact],
    path: str | Path = "data/output/contacts.json",
) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    data = [contact.model_dump(mode="json") for contact in contacts]

    file_path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def load_contacts(
    path: str | Path = "data/output/contacts.json",
) -> list[Contact]:
    file_path = Path(path)

    if not file_path.exists():
        return []

    data = json.loads(file_path.read_text(encoding="utf-8"))

    return [Contact.model_validate(item) for item in data]


class ApplicationTracker:
    def __init__(
        self,
        path: str | Path = "data/output/applications.json",
        *,
        persistence_path: str | Path | None = None,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self.path = Path(path)
        self.persistence_path = (
            Path(persistence_path)
            if persistence_path is not None
            else self.path.with_name(f"{self.path.stem}.submission_state.sqlite3")
        )
        self.submission_state = SQLiteSubmissionState(
            self.persistence_path, busy_timeout_ms=busy_timeout_ms
        )

    def list(self) -> list[Application]:
        applications = load_applications(self.path)
        completions = self.submission_state.list_completions()
        by_url = {item.normalized_job_url: item for item in completions}
        seen: set[str] = set()
        projected: list[Application] = []
        for application in applications:
            normalized_url = normalize_job_url(application.job_url)
            completion = by_url.get(normalized_url)
            if completion is not None:
                application = self._apply_completion(application, completion)
                seen.add(normalized_url)
            projected.append(application)
        missing = set(by_url) - seen
        if missing:
            raise ValueError(
                "SQLite completion has no matching application record; recovery is required"
            )
        return projected

    def get(self, job_url: str) -> Application | None:
        normalized_job_url = normalize_job_url(job_url)

        for application in self.list():
            if normalize_job_url(application.job_url) == normalized_job_url:
                return application

        return None

    def create(
        self,
        company: str,
        job_title: str,
        job_url: str,
        *,
        contact_name: str | None = None,
        contact_email: str | None = None,
        notes: str | None = None,
    ) -> Application:
        applications = self.list()
        normalized_job_url = normalize_job_url(job_url)

        existing = next(
            (
                application
                for application in applications
                if normalize_job_url(application.job_url) == normalized_job_url
            ),
            None,
        )

        if existing is not None:
            raise ValueError(
                f"Application already exists for job URL: {normalized_job_url}"
            )

        application = Application(
            company=company,
            job_title=job_title,
            job_url=normalized_job_url,
            status=ApplicationStatus.discovered,
            contact_name=contact_name,
            contact_email=contact_email,
            notes=notes,
        )

        applications.append(application)
        save_applications(applications, self.path)

        return application

    def update_status(
        self,
        job_url: str,
        status: ApplicationStatus,
        *,
        applied_date: date | None = None,
    ) -> Application:
        if status == ApplicationStatus.applied:
            raise ValueError(
                "Use mark_submitted after submission has been verified"
            )

        applications = self.list()
        normalized_job_url = normalize_job_url(job_url)

        for application in applications:
            if normalize_job_url(application.job_url) != normalized_job_url:
                continue

            application.transition_to(status)

            if applied_date is not None:
                application.applied_date = applied_date

            save_applications(applications, self.path)

            return application

        raise ValueError(
            f"Application not found for job URL: {job_url}"
        )

    def update_automation_status(
        self,
        job_url: str,
        status: AutomationStatus,
    ) -> Application:
        normalized_job_url = normalize_job_url(job_url)
        applications = self.list()

        for application in applications:
            if normalize_job_url(application.job_url) != normalized_job_url:
                continue

            application.transition_automation_to(status)
            save_applications(applications, self.path)
            return application

        raise ValueError(f"Application not found for job URL: {job_url}")

    def mark_submitted(
        self,
        job_url: str,
        *,
        verified_evidence: VerifiedSubmissionEvidence,
        attempt: ApplicationAttempt,
        attempts: AttemptStore,
        applied_date: date | None = None,
    ) -> Application:
        normalized_job_url = normalize_job_url(job_url)
        expected_application_id = application_id_for(normalized_job_url)
        if not verified_evidence.validates_for(
            application_id=expected_application_id,
            job_url=normalized_job_url,
            attempt_number=attempt.attempt_number,
            payload_fingerprint=attempt.payload_fingerprint or "",
        ):
            raise ValueError("Verified submission evidence is not bound to this attempt")
        if (
            attempt.application_id != expected_application_id
            or normalize_job_url(attempt.job_url) != normalized_job_url
        ):
            raise ValueError("A matching persistent submission claim is required")
        with self._locked():
            existing = self.submission_state.get_for_application(expected_application_id)
            applications = load_applications(self.path)
            for index, application in enumerate(applications):
                if normalize_job_url(application.job_url) != normalized_job_url:
                    continue
                if existing is not None:
                    self._validate_completion_match(
                        existing, attempt, verified_evidence
                    )
                    applications[index] = self._apply_completion(
                        application, existing
                    )
                    self._repair_attempt_completion(existing, attempts)
                    save_applications(applications, self.path)
                    return applications[index]
                if application.automation_status != AutomationStatus.submitting:
                    raise ValueError(
                        "Application must be submitting before recording success"
                    )
                if application.status not in {
                    ApplicationStatus.discovered,
                    ApplicationStatus.ready,
                }:
                    raise ValueError(
                        f"Cannot mark application submitted from status: "
                        f"{application.status.value}"
                    )
                if not attempts.matches_persisted_verified_evidence(
                    attempt, verified_evidence, consumed=False
                ):
                    raise ValueError(
                        "Trusted persisted browser-action evidence is required"
                    )

                if application.status == ApplicationStatus.discovered:
                    application.transition_to(ApplicationStatus.ready)
                if application.status == ApplicationStatus.ready:
                    application.transition_to(
                        ApplicationStatus.applied,
                        submission_evidence=verified_evidence.evidence,
                    )
                application.applied_date = applied_date or date.today()
                application.transition_automation_to(AutomationStatus.submitted)
                completion, _created = self.submission_state.commit_completion(
                    application_id=expected_application_id,
                    attempt_number=attempt.attempt_number,
                    job_url=attempt.job_url,
                    payload_fingerprint=attempt.payload_fingerprint or "",
                    browser_action_id=verified_evidence.observation.browser_action_id,
                    evidence=verified_evidence,
                    applied_date=application.applied_date,
                )
                applications[index] = self._apply_completion(application, completion)
                self._repair_attempt_completion(completion, attempts)
                save_applications(applications, self.path)
                return applications[index]

        raise ValueError(f"Application not found for job URL: {job_url}")

    def recover_consumed_submission(
        self, job_url: str, *, attempts: AttemptStore
    ) -> Application | None:
        """Repair JSON projections from a committed SQLite completion only."""
        normalized_job_url = normalize_job_url(job_url)
        application_id = application_id_for(normalized_job_url)
        completion = self.submission_state.get_for_application(application_id)
        if completion is None:
            return None
        with self._locked():
            application = next(
                (
                    item
                    for item in load_applications(self.path)
                    if normalize_job_url(item.job_url) == normalized_job_url
                ),
                None,
            )
            if application is None:
                raise ValueError("SQLite completion has no matching application record")
            self._validate_application_completion(application, completion)
            self._repair_attempt_completion(completion, attempts)
            repaired = self._apply_completion(application, completion)
            save_applications(
                [
                    repaired
                    if normalize_job_url(item.job_url) == normalized_job_url
                    else item
                    for item in load_applications(self.path)
                ],
                self.path,
            )
            return repaired

    @staticmethod
    def _validate_completion_match(
        completion: SubmissionCompletion,
        attempt: ApplicationAttempt,
        evidence: VerifiedSubmissionEvidence,
    ) -> None:
        if (
            completion.application_id != attempt.application_id
            or completion.attempt_number != attempt.attempt_number
            or normalize_job_url(completion.job_url) != normalize_job_url(attempt.job_url)
            or completion.payload_fingerprint != attempt.payload_fingerprint
            or completion.evidence != evidence
            or completion.evidence_id != SQLiteSubmissionState.evidence_id(evidence)
            or completion.browser_action_id != evidence.observation.browser_action_id
        ):
            raise ValueError("Conflicting SQLite completion does not match this attempt")

    @staticmethod
    def _validate_application_completion(
        application: Application, completion: SubmissionCompletion
    ) -> None:
        if (
            application_id_for(application.job_url) != completion.application_id
            or normalize_job_url(application.job_url) != completion.normalized_job_url
            or (
                application.submission_evidence is not None
                and application.submission_evidence != completion.evidence.evidence
            )
        ):
            raise ValueError("Application JSON conflicts with its SQLite completion")

    @classmethod
    def _apply_completion(
        cls, application: Application, completion: SubmissionCompletion
    ) -> Application:
        cls._validate_application_completion(application, completion)
        if application.status in {ApplicationStatus.discovered, ApplicationStatus.ready}:
            if application.automation_status != AutomationStatus.submitting:
                raise ValueError("Stale application state conflicts with SQLite completion")
            return application.model_copy(
                update={
                    "status": completion.application_status,
                    "automation_status": completion.automation_status,
                    "submission_evidence": completion.evidence.evidence,
                    "applied_date": completion.applied_date,
                }
            )
        if (
            application.status == ApplicationStatus.applied
            and application.automation_status == AutomationStatus.submitted
            and application.submission_evidence == completion.evidence.evidence
        ):
            return application
        if (
            application.status
            in {
                ApplicationStatus.interview,
                ApplicationStatus.offer,
                ApplicationStatus.rejected,
                ApplicationStatus.withdrawn,
            }
            and application.automation_status == AutomationStatus.submitted
            and application.submission_evidence == completion.evidence.evidence
        ):
            return application
        raise ValueError("Application state conflicts with SQLite completion")

    @staticmethod
    def _repair_attempt_completion(
        completion: SubmissionCompletion, attempts: AttemptStore
    ) -> None:
        attempt = attempts.latest_for(completion.application_id)
        if attempt is None:
            raise ValueError("SQLite completion has no matching attempt record")
        if not attempts.matches_persisted_verified_evidence(
            attempt, completion.evidence, consumed=None
        ):
            raise ValueError("SQLite completion conflicts with persisted attempt evidence")
        attempts.update(
            attempt,
            outcome=AttemptOutcome.submitted,
            submission_outcome=SubmissionOutcome.submitted,
            verification_result=True,
            verification_evidence=completion.evidence.evidence,
            verified_evidence_consumed=True,
            completed_at=completion.completed_at,
        )

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

    def resolve_unknown_submission(
        self,
        job_url: str,
        *,
        submitted: bool,
        evidence: str,
    ) -> Application:
        if not evidence.strip():
            raise ValueError("Reconciliation evidence must not be empty")

        normalized_job_url = normalize_job_url(job_url)
        applications = self.list()

        for application in applications:
            if normalize_job_url(application.job_url) != normalized_job_url:
                continue
            if (
                application.automation_status
                != AutomationStatus.unknown_submission_result
            ):
                raise ValueError("Application does not have an unknown submission result")

            if submitted:
                if application.status == ApplicationStatus.discovered:
                    application.transition_to(ApplicationStatus.ready)
                if application.status == ApplicationStatus.ready:
                    application.transition_to(
                        ApplicationStatus.applied,
                        submission_evidence=evidence,
                    )
                elif application.status != ApplicationStatus.applied:
                    raise ValueError(
                        f"Cannot resolve submission from application status: "
                        f"{application.status.value}"
                    )
                application.applied_date = date.today()
                application.automation_status = AutomationStatus.submitted
            else:
                application.automation_status = AutomationStatus.failed

            save_applications(applications, self.path)
            return application

        raise ValueError(f"Application not found for job URL: {job_url}")
