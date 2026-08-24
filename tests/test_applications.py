from datetime import date

import pytest

from src.applications.models import Application, ApplicationStatus


def test_application_defaults():
    application = Application(
        company="Acme",
        job_title="Android Developer",
        job_url="https://example.com/job",
    )

    assert application.status == ApplicationStatus.discovered
    assert application.applied_date is None


def test_application_strips_fields():
    application = Application(
        company=" Acme ",
        job_title=" Android Developer ",
        job_url=" https://example.com/job ",
        contact_name=" Recruiter ",
        contact_email=" recruiter@example.com ",
        notes=" Some notes ",
        applied_date=date(2026, 8, 23),
    )

    assert application.company == "Acme"
    assert application.job_title == "Android Developer"
    assert application.contact_email == "recruiter@example.com"
    assert application.notes == "Some notes"


def test_application_rejects_blank_required_field():
    with pytest.raises(ValueError):
        Application(
            company=" ",
            job_title="Android Developer",
            job_url="https://example.com/job",
        )