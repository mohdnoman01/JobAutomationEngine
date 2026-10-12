import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
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
from src.applications.recovery import ApplicationAttempt, AttemptOutcome, AttemptStore
from src.applications.tracker import (
    ApplicationTracker,
    load_applications,
    load_contacts,
    save_applications,
    save_contacts,
)
import src.applications.tracker as tracker_module
from src.research.models import Contact, ContactQualification


def _attempt_store_process_worker(
    path, mode, barrier, result_queue, attempt_data=None, evidence_data=None,
    applications_path=None,
):
    try:
        allow_fixture = mode in {"consume", "complete"}
        attempts = AttemptStore(path, allow_test_fixture_evidence=allow_fixture)
        barrier.wait(timeout=20)
        if mode == "claim":
            attempt = ApplicationAttempt.model_validate(attempt_data)
            result = attempts.claim_submission(
                application_id=attempt.application_id,
                company=attempt.company,
                job_title=attempt.job_title,
                job_url=attempt.job_url,
                platform=attempt.platform,
                attempt=attempt,
                payload_fingerprint=attempt.payload_fingerprint,
            ) is not None
        elif mode == "action":
            attempt = ApplicationAttempt.model_validate(attempt_data)
            result = attempts.begin_browser_submission_action(
                application_id=attempt.application_id,
                job_url=attempt.job_url,
                attempt_number=attempt.attempt_number,
                payload_fingerprint=attempt.payload_fingerprint,
            ) is not None
        elif mode == "consume":
            attempt = ApplicationAttempt.model_validate(attempt_data)
            evidence = VerifiedSubmissionEvidence.model_validate(evidence_data)
            result = attempts.consume_verified_evidence(attempt, evidence)
        elif mode == "complete":
            attempt = ApplicationAttempt.model_validate(attempt_data)
            evidence = VerifiedSubmissionEvidence.model_validate(evidence_data)
            ApplicationTracker(applications_path).mark_submitted(
                attempt.job_url,
                verified_evidence=evidence,
                attempt=attempt,
                attempts=attempts,
            )
            result = True
        else:  # pragma: no cover - test helper misuse
            raise AssertionError(f"Unknown process operation: {mode}")
        result_queue.put(("ok", result))
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))


def _crash_while_holding_attempt_lock(path, acquired):
    with AttemptStore(path)._locked():
        acquired.set()
        os._exit(23)


def _run_two_process_race(
    tmp_path, mode, *, attempt=None, evidence=None, applications_path=None
):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    args = (
        str(tmp_path / "attempts.json"),
        mode,
        barrier,
        result_queue,
        attempt.model_dump(mode="json") if attempt else None,
        evidence.model_dump(mode="json") if evidence else None,
        str(applications_path) if applications_path else None,
    )
    processes = [context.Process(target=_attempt_store_process_worker, args=args) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        results = [result_queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
        assert all(process.exitcode == 0 for process in processes)
        assert all(result[0] == "ok" for result in results), results
        return [result[1] for result in results]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()


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
    browser_action_id = attempts.begin_browser_submission_action(
        application_id=application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
    )
    assert browser_action_id is not None
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="fixture-observation-1",
        browser_action_id=browser_action_id,
        observed_url="file:///local/confirmation.html",
        application_id=application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        action_started=True,
        page_changed_after_action=True,
    )
    assert attempts.record_browser_observation(observation)
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
    assert attempts.record_verified_evidence(attempt, verified)
    return verified, attempts.latest_for(application_id)


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

    attempts = AttemptStore(
        path.with_name("attempts.json"), allow_test_fixture_evidence=True
    )
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
        attempts = AttemptStore(
            path.with_name("attempts.json"), allow_test_fixture_evidence=True
        )
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


@pytest.mark.parametrize(
    "mutation",
    ["other_application", "other_job", "old_attempt", "other_fingerprint", "empty_evidence"],
)
def test_tracker_rejects_unbound_or_empty_verified_evidence(tmp_path, mutation):
    path = tmp_path / "applications.json"
    job_url = "https://example.com/android"
    tracker = ApplicationTracker(path)
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(
        path.with_name("attempts.json"), allow_test_fixture_evidence=True
    )
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)
    if mutation == "other_application":
        evidence = evidence.model_copy(update={"application_id": "other-application"})
    elif mutation == "other_job":
        evidence = evidence.model_copy(update={"job_url": "https://example.com/other"})
    elif mutation == "old_attempt":
        evidence = evidence.model_copy(update={"attempt_number": attempt.attempt_number + 1})
    elif mutation == "other_fingerprint":
        evidence = evidence.model_copy(update={"payload_fingerprint": "other-payload"})
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


def test_caller_constructed_evidence_before_action_consumption_cannot_mark_submitted(tmp_path):
    job_url = "https://example.com/android"
    tracker = ApplicationTracker(tmp_path / "applications.json")
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    attempt = attempts.claim_submission(
        application_id=application_id_for(job_url),
        company="Test Startup",
        job_title="Android Developer",
        job_url=job_url,
        platform="local_fixture",
        payload_fingerprint="fixture-payload-fingerprint",
    )
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="forged-observation",
        browser_action_id="forged-action",
        observed_url="file:///local/confirmation.html",
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        action_started=True,
        page_changed_after_action=True,
    )
    forged = VerifiedSubmissionEvidence(
        mechanism="fixture",
        evidence="matching confirmation phrase",
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        trusted_confirmation_url=observation.observed_url,
        observation=observation,
    )

    with pytest.raises(ValueError, match="persisted browser-action evidence"):
        tracker.mark_submitted(
            job_url, verified_evidence=forged, attempt=attempt, attempts=attempts
        )
    assert tracker.get(job_url).status == ApplicationStatus.ready


def test_fixture_only_evidence_is_rejected_by_default_store(tmp_path):
    job_url = "https://example.com/android"
    tracker = ApplicationTracker(tmp_path / "applications.json")
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(tmp_path / "attempts.json")
    attempt = attempts.claim_submission(
        application_id=application_id_for(job_url),
        company="Test Startup",
        job_title="Android Developer",
        job_url=job_url,
        platform="local_fixture",
        payload_fingerprint="fixture-payload-fingerprint",
    )
    browser_action_id = attempts.begin_browser_submission_action(
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
    )
    assert browser_action_id is not None
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="fixture-observation",
        browser_action_id=browser_action_id,
        observed_url="file:///local/confirmation.html",
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        action_started=True,
        page_changed_after_action=True,
    )
    assert not attempts.record_browser_observation(observation)
    fixture_evidence = VerifiedSubmissionEvidence(
        mechanism="fixture",
        evidence="local fixture confirmation",
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        trusted_confirmation_url=observation.observed_url,
        observation=observation,
    )
    with pytest.raises(ValueError):
        tracker.mark_submitted(
            job_url,
            verified_evidence=fixture_evidence,
            attempt=attempt,
            attempts=attempts,
        )
    assert tracker.get(job_url).status == ApplicationStatus.ready


def test_consumed_evidence_recovery_is_idempotent_for_same_transition(tmp_path):
    job_url = "https://example.com/android"
    tracker = ApplicationTracker(tmp_path / "applications.json")
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)

    tracker.mark_submitted(
        job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
    )
    repeated = tracker.mark_submitted(
        job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
    )
    assert repeated.status == ApplicationStatus.applied
    assert repeated.automation_status == AutomationStatus.submitted
    with pytest.raises(ValueError):
        tracker.mark_submitted(
            job_url,
            verified_evidence=evidence.model_copy(update={"mechanism": "replayed"}),
            attempt=attempt,
            attempts=attempts,
        )


def test_evidence_consumption_is_atomic_and_one_use(tmp_path):
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    evidence, attempt = _fixture_verified_evidence(
        attempts, "https://example.com/android"
    )
    stores = [
        AttemptStore(tmp_path / "attempts.json", allow_test_fixture_evidence=True)
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda store: store.consume_verified_evidence(attempt, evidence), stores
            )
        )
    assert sum(outcomes) == 1


@pytest.mark.parametrize("persisted_state", ["missing", "corrupt"])
def test_missing_or_corrupt_persisted_evidence_state_fails_closed(
    tmp_path, persisted_state
):
    job_url = "https://example.com/android"
    app_path = tmp_path / "applications.json"
    attempt_path = tmp_path / "attempts.json"
    tracker = ApplicationTracker(app_path)
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(attempt_path, allow_test_fixture_evidence=True)
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)
    if persisted_state == "missing":
        attempt_path.unlink()
        expected_error = ValueError
    else:
        attempt_path.write_text("{corrupt", encoding="utf-8")
        import json

        expected_error = json.JSONDecodeError

    with pytest.raises(expected_error):
        tracker.mark_submitted(
            job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
        )
    assert tracker.get(job_url).status == ApplicationStatus.ready


def test_completed_claim_cannot_authorize_a_replayed_browser_action(tmp_path):
    job_url = "https://example.com/android"
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
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


def test_independent_processes_cannot_claim_the_same_attempt(tmp_path):
    attempts = AttemptStore(tmp_path / "attempts.json")
    attempt = attempts.start(
        application_id="app-process-claim",
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/process-claim",
        platform="local_fixture",
    ).model_copy(update={"payload_fingerprint": "process-payload"})

    results = _run_two_process_race(tmp_path, "claim", attempt=attempt)

    assert sum(results) == 1
    assert attempts.latest_for(attempt.application_id).outcome == AttemptOutcome.submitting


def test_independent_processes_cannot_consume_same_browser_action(tmp_path):
    attempts = AttemptStore(tmp_path / "attempts.json")
    attempt = attempts.claim_submission(
        application_id="app-process-action",
        company="Test Startup",
        job_title="Android Developer",
        job_url="https://example.com/process-action",
        platform="local_fixture",
        payload_fingerprint="process-payload",
    )

    results = _run_two_process_race(tmp_path, "action", attempt=attempt)

    assert sum(results) == 1
    stored = attempts.latest_for(attempt.application_id)
    assert stored.browser_action_started is True
    assert stored.browser_action_id


def test_independent_processes_cannot_consume_same_verified_evidence(tmp_path):
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    evidence, attempt = _fixture_verified_evidence(
        attempts, "https://example.com/process-evidence"
    )

    results = _run_two_process_race(
        tmp_path, "consume", attempt=attempt, evidence=evidence
    )

    assert sum(results) == 1
    assert attempts.latest_for(attempt.application_id).verified_evidence_consumed


def test_independent_processes_recover_same_completion_idempotently(tmp_path):
    job_url = "https://example.com/process-completion"
    applications_path = tmp_path / "applications.json"
    tracker = ApplicationTracker(applications_path)
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)

    results = _run_two_process_race(
        tmp_path,
        "complete",
        attempt=attempt,
        evidence=evidence,
        applications_path=applications_path,
    )

    assert results == [True, True]
    completed = ApplicationTracker(applications_path).get(job_url)
    assert completed.status == ApplicationStatus.applied
    assert completed.automation_status == AutomationStatus.submitted


def test_process_exit_releases_attempt_store_lock(tmp_path):
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    path = tmp_path / "attempts.json"
    process = context.Process(
        target=_crash_while_holding_attempt_lock,
        args=(str(path), acquired),
    )
    process.start()
    try:
        assert acquired.wait(timeout=20)
        process.join(timeout=20)
        assert process.exitcode == 23
        assert AttemptStore(path).start(
            application_id="after-crash",
            company="Test Startup",
            job_title="Android Developer",
            job_url="https://example.com/after-crash",
            platform="local_fixture",
        )
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def _read_corrupt_attempt_store(path, result_queue):
    try:
        AttemptStore(path).list()
    except BaseException as exc:
        result_queue.put(type(exc).__name__)
    else:  # pragma: no cover - malformed state must fail closed
        result_queue.put("accepted")


@pytest.mark.parametrize("contents", ["{corrupt", "["])
def test_independent_process_rejects_corrupt_or_incomplete_attempt_state(
    tmp_path, contents
):
    context = multiprocessing.get_context("spawn")
    path = tmp_path / "attempts.json"
    path.write_text(contents, encoding="utf-8")
    result_queue = context.Queue()
    process = context.Process(
        target=_read_corrupt_attempt_store,
        args=(str(path), result_queue),
    )
    process.start()
    try:
        result = result_queue.get(timeout=20)
        process.join(timeout=20)
        assert process.exitcode == 0
        assert result == "JSONDecodeError"
        assert path.read_text(encoding="utf-8") == contents
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()


def test_tracker_recovers_consumed_evidence_after_local_write_failure(
    tmp_path, monkeypatch
):
    job_url = "https://example.com/recover-consumed"
    tracker = ApplicationTracker(tmp_path / "applications.json")
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)

    def fail_tracker_write(*args, **kwargs):
        raise OSError("injected crash before tracker replacement")

    monkeypatch.setattr(tracker_module, "save_applications", fail_tracker_write)
    with pytest.raises(OSError, match="injected crash"):
        tracker.mark_submitted(
            job_url,
            verified_evidence=evidence,
            attempt=attempt,
            attempts=attempts,
        )
    consumed = attempts.latest_for(attempt.application_id)
    assert consumed.verified_evidence_consumed is True
    assert tracker.get(job_url).automation_status == AutomationStatus.submitted
    assert tracker.get(job_url).status == ApplicationStatus.applied

    monkeypatch.undo()
    recovered = tracker.recover_consumed_submission(job_url, attempts=attempts)
    repeated = tracker.recover_consumed_submission(job_url, attempts=attempts)
    assert recovered.status == ApplicationStatus.applied
    assert repeated == recovered
    assert tracker.get(job_url).status == ApplicationStatus.applied
    assert attempts.latest_for(attempt.application_id).verified_evidence_consumed is True


def test_repeated_recovery_is_idempotent_after_tracker_write(tmp_path):
    job_url = "https://example.com/recover-after-write"
    tracker = ApplicationTracker(tmp_path / "applications.json")
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)
    tracker.mark_submitted(
        job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
    )

    first = tracker.recover_consumed_submission(job_url, attempts=attempts)
    second = tracker.recover_consumed_submission(job_url, attempts=attempts)

    assert first == second
    assert first.status == ApplicationStatus.applied
    assert first.automation_status == AutomationStatus.submitted


def test_recovery_fails_closed_for_conflicting_unknown_attempt(tmp_path):
    job_url = "https://example.com/recover-conflict"
    tracker = ApplicationTracker(tmp_path / "applications.json")
    tracker.create("Test Startup", "Android Developer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)
    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    evidence, attempt = _fixture_verified_evidence(attempts, job_url)
    attempts.update(
        attempt,
        outcome=AttemptOutcome.unknown_submission_result,
        verified_evidence_consumed=True,
    )

    assert tracker.recover_consumed_submission(job_url, attempts=attempts) is None
    assert tracker.get(job_url).status == ApplicationStatus.ready
    assert tracker.get(job_url).automation_status == AutomationStatus.submitting
