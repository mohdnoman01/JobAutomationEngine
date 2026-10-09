from datetime import date

import pytest

from src.applications.models import (
    Application,
    ApplicationStatus,
    AutomationStatus,
)
from src.applications.evidence import (
    BrowserObservation,
    EvidenceObserver,
    VerifiedSubmissionEvidence,
)
from src.applications.identity import application_id_for
from src.applications.recovery import AttemptOutcome, AttemptStore
from src.applications.tracker import (
    ApplicationTracker,
    load_applications,
    load_contacts,
    save_applications,
    save_contacts,
)
from src.research.models import Contact, ContactQualification


def _fixture_verified_evidence(attempts, job_url, *, evidence="Local fixture confirmation"):
    application_id = application_id_for(job_url)
    attempt = attempts.claim_submission(
        application_id=application_id,
        company="Test Startup",
        job_title="Android Developer",
        job_url=job_url,
        platform="local_fixture",
        payload_fingerprint="fixture-payload-fingerprint",
    )
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="fixture-observation-1",
        observed_url="file:///local/confirmation.html",
        application_id=application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        action_started=True,
        page_changed_after_action=True,
    )
    verified = VerifiedSubmissionEvidence(
        mechanism="local_fixture",
        evidence=evidence,
        application_id=application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        trusted_confirmation_url=observation.observed_url,
        observation=observation,
    )
    return verified, attempt


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

    tracker.update_status(
        "https://example.com/android",
        ApplicationStatus.ready,
    )

    tracker.update_automation_status(
        "https://example.com/android",
        AutomationStatus.preparing,
    )
    tracker.update_automation_status(
        "https://example.com/android",
        AutomationStatus.ready_to_submit,
    )
    tracker.update_automation_status(
        "https://example.com/android",
        AutomationStatus.submitting,
    )

    attempts = AttemptStore(path.with_name("attempts.json"))
    verified_evidence, attempt = _fixture_verified_evidence(
        attempts, "https://example.com/android"
    )
    updated = tracker.mark_submitted(
        "https://example.com/android",
        applied_date=date(2026, 8, 24),
        verified_evidence=verified_evidence,
        attempt=attempt,
        attempts=attempts,
    )

    assert updated.status == ApplicationStatus.applied
    assert updated.applied_date == date(2026, 8, 24)


def _application_at_status(application, status):
    paths = {
        ApplicationStatus.discovered: [],
        ApplicationStatus.ready: [ApplicationStatus.ready],
        ApplicationStatus.applied: [
            ApplicationStatus.ready,
            ApplicationStatus.applied,
        ],
        ApplicationStatus.interview: [
            ApplicationStatus.ready,
            ApplicationStatus.applied,
            ApplicationStatus.interview,
        ],
        ApplicationStatus.rejected: [ApplicationStatus.rejected],
        ApplicationStatus.withdrawn: [
            ApplicationStatus.ready,
            ApplicationStatus.withdrawn,
        ],
        ApplicationStatus.offer: [
            ApplicationStatus.ready,
            ApplicationStatus.applied,
            ApplicationStatus.interview,
            ApplicationStatus.offer,
        ],
    }

    for next_status in paths[status]:
        if next_status == ApplicationStatus.applied:
            application.transition_to(
                next_status,
                submission_evidence="Test submission confirmation",
            )
        else:
            application.transition_to(next_status)

    return application


@pytest.mark.parametrize(
    ("initial_status", "next_status"),
    [
        (ApplicationStatus.discovered, ApplicationStatus.ready),
        (ApplicationStatus.ready, ApplicationStatus.applied),
        (ApplicationStatus.applied, ApplicationStatus.interview),
        (ApplicationStatus.interview, ApplicationStatus.offer),
        (ApplicationStatus.discovered, ApplicationStatus.rejected),
        (ApplicationStatus.ready, ApplicationStatus.rejected),
        (ApplicationStatus.ready, ApplicationStatus.withdrawn),
        (ApplicationStatus.applied, ApplicationStatus.rejected),
        (ApplicationStatus.applied, ApplicationStatus.withdrawn),
        (ApplicationStatus.interview, ApplicationStatus.rejected),
        (ApplicationStatus.interview, ApplicationStatus.withdrawn),
    ],
)
def test_application_tracker_allows_valid_transitions(
    tmp_path,
    initial_status,
    next_status,
):
    path = tmp_path / "applications.json"
    tracker = ApplicationTracker(path)
    application = tracker.create(
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/android",
    )
    _application_at_status(application, initial_status)
    save_applications([application], path)

    if next_status == ApplicationStatus.applied:
        tracker.update_automation_status(
            "https://example.com/android",
            AutomationStatus.preparing,
        )
        tracker.update_automation_status(
            "https://example.com/android",
            AutomationStatus.ready_to_submit,
        )
        tracker.update_automation_status(
            "https://example.com/android",
            AutomationStatus.submitting,
        )
        attempts = AttemptStore(path.with_name("attempts.json"))
        verified_evidence, attempt = _fixture_verified_evidence(
            attempts, "https://example.com/android"
        )
        updated = tracker.mark_submitted(
            "https://example.com/android",
            verified_evidence=verified_evidence,
            attempt=attempt,
            attempts=attempts,
        )
    else:
        updated = tracker.update_status(
            "https://example.com/android",
            next_status,
        )

    assert updated.status == next_status


@pytest.mark.parametrize(
    ("initial_status", "next_status"),
    [
        (ApplicationStatus.discovered, ApplicationStatus.applied),
        (ApplicationStatus.ready, ApplicationStatus.interview),
        (ApplicationStatus.applied, ApplicationStatus.offer),
        (ApplicationStatus.offer, ApplicationStatus.rejected),
        (ApplicationStatus.rejected, ApplicationStatus.ready),
        (ApplicationStatus.withdrawn, ApplicationStatus.applied),
    ],
)
def test_application_tracker_rejects_invalid_transitions(
    tmp_path,
    initial_status,
    next_status,
):
    path = tmp_path / "applications.json"
    tracker = ApplicationTracker(path)
    application = tracker.create(
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/android",
    )
    _application_at_status(application, initial_status)
    save_applications([application], path)

    with pytest.raises(ValueError):
        tracker.update_status(
            "https://example.com/android",
            next_status,
        )

    assert tracker.get("https://example.com/android").status == initial_status


@pytest.mark.parametrize(
    "terminal_status",
    [
        ApplicationStatus.offer,
        ApplicationStatus.rejected,
        ApplicationStatus.withdrawn,
    ],
)
def test_application_tracker_terminal_states_reject_updates(
    tmp_path,
    terminal_status,
):
    path = tmp_path / "applications.json"
    tracker = ApplicationTracker(path)
    application = tracker.create(
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/android",
    )
    _application_at_status(application, terminal_status)
    save_applications([application], path)

    with pytest.raises(ValueError, match="Invalid application status transition"):
        tracker.update_status(
            "https://example.com/android",
            ApplicationStatus.ready,
        )


def test_application_tracker_rejects_duplicate_job(tmp_path):
    path = tmp_path / "applications.json"

    tracker = ApplicationTracker(path)

    tracker.create(
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/android#apply",
    )

    with pytest.raises(ValueError):
        tracker.create(
            company="Test Startup",
            job_title="Android Developer",
            job_url="https://example.com/android",
        )


def test_application_tracker_requires_evidence_to_mark_applied(tmp_path):
    path = tmp_path / "applications.json"
    tracker = ApplicationTracker(path)
    tracker.create(
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/android",
    )
    tracker.update_status(
        "https://example.com/android",
        ApplicationStatus.ready,
    )

    with pytest.raises(ValueError, match="Use mark_submitted"):
        tracker.update_status(
            "https://example.com/android",
            ApplicationStatus.applied,
        )


@pytest.mark.parametrize("mutation", ["other_application", "other_job", "old_attempt", "empty_evidence"])
def test_tracker_rejects_unbound_or_empty_verified_evidence(tmp_path, mutation):
    path = tmp_path / "applications.json"
    job_url = "https://example.com/android"
    tracker = ApplicationTracker(path)
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(path.with_name("attempts.json"))
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)
    if mutation == "other_application":
        evidence = evidence.model_copy(update={"application_id": "other-application"})
    elif mutation == "other_job":
        evidence = evidence.model_copy(update={"job_url": "https://example.com/other"})
    elif mutation == "old_attempt":
        evidence = evidence.model_copy(update={"attempt_number": attempt.attempt_number + 1})
    else:
        evidence = evidence.model_copy(update={"evidence": "  "})

    with pytest.raises(ValueError):
        tracker.mark_submitted(
            job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
        )
    assert tracker.get(job_url).status == ApplicationStatus.ready


def test_tracker_does_not_accept_arbitrary_string_as_verified_evidence(tmp_path):
    tracker = ApplicationTracker(tmp_path / "applications.json")
    job_url = "https://example.com/android"
    tracker.create("Test Startup", "Android Developer", job_url)
    with pytest.raises((TypeError, ValueError)):
        tracker.mark_submitted(job_url, verified_evidence="confirmation")


def test_completed_claim_cannot_authorize_a_replayed_browser_action(tmp_path):
    job_url = "https://example.com/android"
    attempts = AttemptStore(tmp_path / "attempts.json")
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)
    attempts.update(
        attempt,
        outcome=AttemptOutcome.submitted,
        verification_result=True,
        verification_evidence=evidence.evidence,
    )

    assert not attempts.matches_submission_claim(
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
    )
    assert attempts.matches_submission_claim(
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        allow_completed_verified=True,
    )


def test_application_tracker_normalizes_legacy_url_on_lookup(tmp_path):
    path = tmp_path / "applications.json"

    save_applications(
        [
            Application(
                company="Test Startup",
                job_title="Android Developer",
                job_url="https://example.com/android#apply",
            )
        ],
        path,
    )

    tracker = ApplicationTracker(path)

    application = tracker.get("https://example.com/android")

    assert application is not None
    assert application.job_url == "https://example.com/android#apply"


def test_application_tracker_raises_for_missing_job(tmp_path):
    path = tmp_path / "applications.json"

    tracker = ApplicationTracker(path)

    with pytest.raises(ValueError):
        tracker.update_status(
            "https://example.com/missing",
            ApplicationStatus.applied,
        )
