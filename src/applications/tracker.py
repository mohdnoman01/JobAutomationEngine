from __future__ import annotations

import json
from pathlib import Path

from src.applications.models import Application
from src.research.models import Contact


from datetime import date

from src.applications.models import Application, ApplicationStatus


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
        for application in self.list():
            if application.job_url == job_url:
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

        existing = next(
            (
                application
                for application in applications
                if application.job_url == job_url
            ),
            None,
        )

        if existing is not None:
            raise ValueError(
                f"Application already exists for job URL: {job_url}"
            )

        application = Application(
            company=company,
            job_title=job_title,
            job_url=job_url,
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
        applications = self.list()

        for application in applications:
            if application.job_url != job_url:
                continue

            application.status = status

            if applied_date is not None:
                application.applied_date = applied_date

            save_applications(applications, self.path)

            return application

        raise ValueError(
            f"Application not found for job URL: {job_url}"
        )