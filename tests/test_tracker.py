from datetime import date

import pytest

from src.applications.models import Application, ApplicationStatus
from src.applications.tracker import (
    ApplicationTracker,
    load_applications,
    load_contacts,
    save_applications,
    save_contacts,
)
from src.research.models import Contact, ContactQualification


def test_application_persistence(tmp_path):
    path = tmp_path / "applications.json"

    applications = [
        Application(
            company="Test Startup",
            job_title="Android Developer",
            job_url="https://example.com/job",
            status=ApplicationStatus.ready,
            applied_date=date(2026, 8, 24),
            notes="Test application",
        )
    ]

    save_applications(applications, path)

    loaded = load_applications(path)

    assert len(loaded) == 1
    assert loaded[0] == applications[0]


def test_contact_persistence(tmp_path):
    path = tmp_path / "contacts.json"

    contacts = [
        Contact(
            name="Test Recruiter",
            email="recruiter@example.com",
            role="Recruiter",
            company="Test Startup",
            source="https://example.com/careers",
            discovery_type="same_domain_page",
            qualification=ContactQualification.eligible,
            qualification_reason="eligible_role:recruit",
        )
    ]

    save_contacts(contacts, path)

    loaded = load_contacts(path)

    assert len(loaded) == 1
    assert loaded[0] == contacts[0]
    assert loaded[0].source_url == "https://example.com/careers"


def test_missing_files_return_empty_lists(tmp_path):
    assert load_applications(tmp_path / "missing.json") == []
    assert load_contacts(tmp_path / "missing.json") == []


def test_application_tracker_creates_and_gets_application(tmp_path):
    path = tmp_path / "applications.json"

    tracker = ApplicationTracker(path)

    created = tracker.create(
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/android",
        contact_name="Test Recruiter",
        contact_email="recruiter@example.com",
    )

    assert created.status == ApplicationStatus.discovered

    loaded = tracker.get("https://example.com/android")

    assert loaded is not None
    assert loaded.company == "Test Startup"
    assert loaded.job_title == "Android Developer"
    assert loaded.contact_email == "recruiter@example.com"


def test_application_tracker_updates_status(tmp_path):
    path = tmp_path / "applications.json"

    tracker = ApplicationTracker(path)

    tracker.create(
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/android",
    )

    updated = tracker.update_status(
        "https://example.com/android",
        ApplicationStatus.applied,
        applied_date=date(2026, 8, 24),
    )

    assert updated.status == ApplicationStatus.applied
    assert updated.applied_date == date(2026, 8, 24)


def test_application_tracker_rejects_duplicate_job(tmp_path):
    path = tmp_path / "applications.json"

    tracker = ApplicationTracker(path)

    tracker.create(
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/android",
    )

    with pytest.raises(ValueError):
        tracker.create(
            company="Test Startup",
            job_title="Android Developer",
            job_url="https://example.com/android",
        )


def test_application_tracker_raises_for_missing_job(tmp_path):
    path = tmp_path / "applications.json"

    tracker = ApplicationTracker(path)

    with pytest.raises(ValueError):
        tracker.update_status(
            "https://example.com/missing",
            ApplicationStatus.applied,
        )
