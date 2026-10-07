from datetime import date

import pytest

from src.applications.models import Application, ApplicationStatus, AutomationStatus


def test_application_defaults():
    application = Application(
        company="Acme",
        job_title="Android Developer",
        job_url="https://example.com/job",
    )

    assert application.status == ApplicationStatus.discovered
    assert application.applied_date is None


def test_legacy_application_data_defaults_automation_status():
    application = Application.model_validate(
        {
            "company": "Acme",
            "job_title": "Android Developer",
            "job_url": "https://example.com/job",
            "status": "applied",
            "applied_date": "2026-08-24",
        }
    )

    assert application.status == ApplicationStatus.applied
    assert application.automation_status.value == "not_started"


def test_automation_status_rejects_invalid_transitions():
    application = Application(
        company="Acme",
        job_title="Android Developer",
        job_url="https://example.com/job",
    )

    with pytest.raises(ValueError, match="Invalid automation status transition"):
        application.transition_automation_to(AutomationStatus.submitted)


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