from __future__ import annotations

import json
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
from src.applications.recovery import ApplicationAttempt, AttemptStore
from src.research.job_normalizer import normalize_job_url


def save_applications(
    applications: list[Application],
    path: str | Path = "data/output/applications.json",
) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    data = [application.model_dump(mode="json") for application in applications]

    file_path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


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
    ) -> None:
        self.path = Path(path)

    def list(self) -> list[Application]:
        return load_applications(self.path)

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
            or not attempts.matches_submission_claim(
                application_id=attempt.application_id,
                job_url=attempt.job_url,
                attempt_number=attempt.attempt_number,
                payload_fingerprint=attempt.payload_fingerprint or "",
                allow_completed_verified=True,
            )
        ):
            raise ValueError("A matching persistent submission claim is required")
        applications = self.list()

        for application in applications:
            if normalize_job_url(application.job_url) != normalized_job_url:
                continue
            if application.automation_status != AutomationStatus.submitting:
                raise ValueError(
                    "Application must be submitting before recording success"
                )

            if application.status == ApplicationStatus.discovered:
                application.transition_to(ApplicationStatus.ready)
            if application.status == ApplicationStatus.ready:
                application.transition_to(
                    ApplicationStatus.applied,
                    submission_evidence=verified_evidence.evidence,
                )
            elif application.status != ApplicationStatus.applied:
                raise ValueError(
                    f"Cannot mark application submitted from status: "
                    f"{application.status.value}"
                )

            application.applied_date = applied_date or date.today()
            application.transition_automation_to(AutomationStatus.submitted)
            save_applications(applications, self.path)
            return application

        raise ValueError(f"Application not found for job URL: {job_url}")

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
