import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.applications.evidence import (
    BrowserObservation,
    EvidenceObserver,
    VerifiedSubmissionEvidence,
)
from src.applications.identity import application_id_for
from src.applications.migration_planner import (
    PlanDisposition,
    SourceState,
    plan_legacy_lifecycle_migration,
)
from src.applications.models import Application, ApplicationStatus, AutomationStatus
from src.applications.persistence import SQLiteSubmissionState
from src.applications.recovery import (
    ApplicationAttempt,
    AttemptOutcome,
    AttemptStore,
    SubmissionOutcome,
)
from src.applications.tracker import ApplicationTracker


def _application(url, *, status=ApplicationStatus.ready, automation=AutomationStatus.needs_review, evidence=None):
    return Application(
        company="Fixture Co",
        job_title="Engineer",
        job_url=url,
        status=status,
        automation_status=automation,
        submission_evidence=evidence,
    ).model_dump(mode="json")


def _attempt(
    url,
    *,
    number=1,
    outcome=AttemptOutcome.failed,
    submission_outcome=SubmissionOutcome.not_started,
    retryable=False,
    **changes,
):
    application_id = application_id_for(url)
    attempt = ApplicationAttempt(
        application_id=application_id,
        company="Fixture Co",
        job_title="Engineer",
        job_url=url,
        platform="fixture",
        attempt_number=number,
        payload_fingerprint="fixture-payload-v1",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        outcome=outcome,
        submission_outcome=submission_outcome,
        retryable=retryable,
        **changes,
    )
    return attempt.model_dump(mode="json")


def _write_json(path, rows):
    path.write_text(json.dumps(rows, sort_keys=True), encoding="utf-8")
    return path.read_bytes()


def _plan(tmp_path, apps, attempts, db=None):
    apps_path = tmp_path / "applications.json"
    attempts_path = tmp_path / "attempts.json"
    _write_json(apps_path, apps)
    _write_json(attempts_path, attempts)
    return plan_legacy_lifecycle_migration(
        applications_path=apps_path,
        attempts_path=attempts_path,
        sqlite_path=db,
    ), apps_path, attempts_path


def test_valid_history_plan_is_deterministic_and_read_only(tmp_path):
    url = "https://example.test/jobs/history"
    app_path = tmp_path / "applications.json"
    attempt_path = tmp_path / "attempts.json"
    db_path = tmp_path / "submission.sqlite3"
    app_bytes = _write_json(
        app_path,
        [_application(url, status=ApplicationStatus.ready, automation=AutomationStatus.needs_review)],
    )
    attempt_bytes = _write_json(attempt_path, [_attempt(url)])
    SQLiteSubmissionState(db_path)
    db_bytes_before = db_path.read_bytes()
    with sqlite3.connect(db_path) as connection:
        rows_before = connection.execute(
            "SELECT COUNT(*) FROM submission_completions"
        ).fetchone()[0]

    kwargs = {
        "applications_path": app_path,
        "attempts_path": attempt_path,
        "sqlite_path": db_path,
    }
    first = plan_legacy_lifecycle_migration(**kwargs)
    second = plan_legacy_lifecycle_migration(**kwargs)

    assert first.canonical_json() == second.canonical_json()
    assert first.importable_count == 2
    assert first.quarantined_count == 0
    assert first.excluded_count == 0
    assert first.sources[0].state == SourceState.present
    assert first.sources[0].records_read == 1
    assert app_path.read_bytes() == app_bytes
    assert attempt_path.read_bytes() == attempt_bytes
    assert db_path.read_bytes() == db_bytes_before
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM submission_completions"
        ).fetchone()[0] == rows_before == 0


def test_missing_inputs_are_distinct_from_valid_empty_files(tmp_path):
    empty_apps = tmp_path / "empty-apps.json"
    empty_attempts = tmp_path / "empty-attempts.json"
    _write_json(empty_apps, [])
    _write_json(empty_attempts, [])
    plan = plan_legacy_lifecycle_migration(
        applications_path=empty_apps,
        attempts_path=empty_attempts,
        sqlite_path=tmp_path / "absent.sqlite3",
    )
    states = {item.category: item.state for item in plan.sources}

    assert states == {
        "applications": SourceState.present,
        "attempts": SourceState.present,
        "sqlite_completions": SourceState.missing,
    }
    assert {item.records_read for item in plan.sources} == {0}
    assert not (tmp_path / "absent.sqlite3").exists()


def test_corrupt_json_and_malformed_records_are_reported(tmp_path):
    apps = tmp_path / "applications.json"
    attempts = tmp_path / "attempts.json"
    apps.write_text("{broken", encoding="utf-8")
    attempts.write_text(json.dumps([{"application_id": "missing-fields"}]), encoding="utf-8")

    plan = plan_legacy_lifecycle_migration(
        applications_path=apps,
        attempts_path=attempts,
        sqlite_path=None,
    )

    assert {item.category: item.state for item in plan.sources}["applications"] == SourceState.corrupt
    assert any("json_source_corrupt" in item.reason_codes for item in plan.records)
    assert any("attempt_record_malformed" in item.reason_codes for item in plan.records)
    assert plan.quarantined_count == 2


def test_duplicate_normalized_urls_quarantine_all_conflicting_rows(tmp_path):
    url = "https://example.test/jobs/duplicate"
    plan, _, _ = _plan(
        tmp_path,
        [
            _application(url, automation=AutomationStatus.needs_review),
            _application(url + "#apply", automation=AutomationStatus.needs_review),
        ],
        [],
    )

    app_records = [item for item in plan.records if item.source_category == "application"]
    assert len(app_records) == 2
    assert all(item.disposition == PlanDisposition.quarantined for item in app_records)
    assert all("duplicate_normalized_job_url" in item.reason_codes for item in app_records)
    assert all(item.conflicts_with for item in app_records)


def test_legacy_success_status_without_matching_sqlite_evidence_is_quarantined(tmp_path):
    url = "https://example.test/jobs/claimed-success"
    plan, _, _ = _plan(
        tmp_path,
        [_application(url, status=ApplicationStatus.applied, automation=AutomationStatus.submitted,
                      evidence="legacy phrase only")],
        [_attempt(url, outcome=AttemptOutcome.submitted,
                  submission_outcome=SubmissionOutcome.submitted,
                  verification_result=True, verification_evidence="legacy phrase only")],
    )

    assert plan.importable_count == 0
    assert any(
        item.source_category == "application"
        and "legacy_success_without_matching_sqlite_completion" in item.reason_codes
        and item.disposition == PlanDisposition.quarantined
        for item in plan.records
    )
    assert any(
        item.source_category == "attempt"
        and item.disposition == PlanDisposition.quarantined
        and "legacy_submitted_attempt_without_matching_completion" in item.reason_codes
        for item in plan.records
    )


def test_claim_observation_and_evidence_are_excluded_and_unknown_stays_blocked(tmp_path):
    url = "https://example.test/jobs/unknown"
    app_id = application_id_for(url)
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="old-observation",
        browser_action_id="old-action",
        observed_url="file:///fixture/confirmation.html",
        application_id=app_id,
        job_url=url,
        attempt_number=1,
        payload_fingerprint="fixture-payload-v1",
        action_started=True,
        page_changed_after_action=True,
    )
    evidence = VerifiedSubmissionEvidence(
        mechanism="fixture",
        evidence="old fixture phrase",
        application_id=app_id,
        job_url=url,
        attempt_number=1,
        payload_fingerprint="fixture-payload-v1",
        trusted_confirmation_url=observation.observed_url,
        observation=observation,
    )
    attempt = _attempt(
        url,
        outcome=AttemptOutcome.unknown_submission_result,
        submission_outcome=SubmissionOutcome.unknown,
        retryable=True,
        browser_action_started=True,
        browser_action_id="old-action",
        browser_observation=observation.model_dump(mode="json"),
        verified_evidence_record=evidence.model_dump(mode="json"),
        verified_evidence_consumed=True,
    )
    plan, _, _ = _plan(
        tmp_path,
        [_application(url, automation=AutomationStatus.unknown_submission_result)],
        [attempt],
    )

    imported_attempt = next(
        item for item in plan.records
        if item.source_category == "attempt" and item.disposition == PlanDisposition.importable
    )
    assert imported_attempt.proposed_import == {
        "application_id": app_id,
        "attempt_number": 1,
        "outcome": AttemptOutcome.unknown_submission_result.value,
        "submission_outcome": SubmissionOutcome.unknown.value,
        "retryable": False,
    }
    assert not {"browser_action_id", "browser_observation", "verified_evidence_record"}.intersection(
        imported_attempt.proposed_import
    )
    assert sum(
        item.disposition == PlanDisposition.excluded
        and item.source_category in {"submission_authorization", "browser_observation", "verified_evidence"}
        for item in plan.records
    ) == 3


def test_malformed_evidence_is_quarantined_not_upgraded(tmp_path):
    url = "https://example.test/jobs/bad-evidence"
    attempt = _attempt(
        url,
        outcome=AttemptOutcome.unknown_submission_result,
        submission_outcome=SubmissionOutcome.unknown,
    )
    attempt["verified_evidence_record"] = {"outcome": "verified"}
    plan, _, _ = _plan(
        tmp_path,
        [_application(url, automation=AutomationStatus.unknown_submission_result)],
        [attempt],
    )

    assert any(
        item.source_category == "verified_evidence"
        and item.disposition == PlanDisposition.quarantined
        and "legacy_evidence_malformed" in item.reason_codes
        for item in plan.records
    )
    assert not any(
        item.proposed_import and item.proposed_import.get("verification_result") is True
        for item in plan.records
    )


def test_evidence_payload_conflict_quarantines_attempt_and_reports_references(tmp_path):
    url = "https://example.test/jobs/conflicting-evidence"
    app_id = application_id_for(url)
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="conflicting-observation",
        browser_action_id="conflicting-action",
        observed_url="file:///fixture/confirmation.html",
        application_id=app_id,
        job_url=url,
        attempt_number=1,
        payload_fingerprint="different-payload",
        action_started=True,
        page_changed_after_action=True,
    )
    attempt = _attempt(
        url,
        outcome=AttemptOutcome.unknown_submission_result,
        submission_outcome=SubmissionOutcome.unknown,
        browser_action_started=True,
        browser_action_id="conflicting-action",
        browser_observation=observation.model_dump(mode="json"),
    )
    plan, _, _ = _plan(
        tmp_path,
        [_application(url, automation=AutomationStatus.unknown_submission_result)],
        [attempt],
    )

    attempt_record = next(item for item in plan.records if item.source_category == "attempt")
    observation_record = next(
        item for item in plan.records if item.source_category == "browser_observation"
    )
    assert attempt_record.disposition == PlanDisposition.quarantined
    assert "attempt_observation_context_conflict" in attempt_record.reason_codes
    assert observation_record.disposition == PlanDisposition.quarantined
    assert observation_record.validation_errors


def test_existing_completion_repairs_interrupted_json_projections_without_reimport(tmp_path, monkeypatch):
    url = "https://example.test/jobs/completed"
    app_path = tmp_path / "applications.json"
    attempt_path = tmp_path / "attempts.json"
    db_path = tmp_path / "applications.sqlite3"
    tracker = ApplicationTracker(app_path, persistence_path=db_path)
    app = tracker.create("Fixture Co", "Engineer", url)
    tracker.update_status(url, ApplicationStatus.ready)
    tracker.update_automation_status(url, AutomationStatus.preparing)
    tracker.update_automation_status(url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(url, AutomationStatus.submitting)

    attempt_store = AttemptStore(
        attempt_path,
        allow_test_fixture_evidence=True,
    )
    attempt = attempt_store.start(
        application_id=application_id_for(url),
        company=app.company,
        job_title=app.job_title,
        job_url=url,
        platform="local_fixture",
    )
    attempt = attempt_store.claim_submission(
        application_id=attempt.application_id,
        company=app.company,
        job_title=app.job_title,
        job_url=url,
        platform="local_fixture",
        attempt=attempt,
        payload_fingerprint="fixture-payload-v1",
    )
    action_id = attempt_store.begin_browser_submission_action(
        application_id=attempt.application_id,
        job_url=url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
    )
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="fixture-completion-observation",
        browser_action_id=action_id,
        observed_url="file:///fixture/completed.html",
        application_id=attempt.application_id,
        job_url=url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        action_started=True,
        page_changed_after_action=True,
    )
    assert attempt_store.record_browser_observation(observation)
    evidence = VerifiedSubmissionEvidence(
        mechanism="fixture",
        evidence="explicit fixture confirmation",
        application_id=attempt.application_id,
        job_url=url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        trusted_confirmation_url=observation.observed_url,
        observation=observation,
    )
    assert attempt_store.record_verified_evidence(attempt, evidence)
    attempt = attempt_store.update(
        attempt,
        current_step="verify",
        submission_outcome=SubmissionOutcome.submitted,
    )
    def interrupt_json_projection(*_args):
        raise OSError("injected after SQLite completion commit")

    monkeypatch.setattr(tracker, "_repair_attempt_completion", interrupt_json_projection)
    with pytest.raises(OSError, match="after SQLite completion commit"):
        tracker.mark_submitted(
            url,
            verified_evidence=evidence,
            attempt=attempt_store.latest_for(attempt.application_id),
            attempts=attempt_store,
        )
    original_app_bytes = app_path.read_bytes()
    original_attempt_bytes = attempt_path.read_bytes()
    original_db_bytes = db_path.read_bytes()

    plan = plan_legacy_lifecycle_migration(
        applications_path=app_path,
        attempts_path=attempt_path,
        sqlite_path=db_path,
    )

    assert plan.quarantined_count == 0, plan.canonical_json()
    assert any(
        item.source_category == "application"
        and item.disposition == PlanDisposition.importable
        and item.proposed_import["business_status"] == ApplicationStatus.applied.value
        for item in plan.records
    )
    assert any(
        item.source_category == "sqlite_completion"
        and item.disposition == PlanDisposition.excluded
        and "completion_already_authoritative_not_imported" in item.reason_codes
        for item in plan.records
    )
    assert app_path.read_bytes() == original_app_bytes
    assert attempt_path.read_bytes() == original_attempt_bytes
    assert db_path.read_bytes() == original_db_bytes


def test_repeated_read_only_planning_creates_no_authorization_or_completion(tmp_path):
    url = "https://example.test/jobs/no-writes"
    plan, _, _ = _plan(
        tmp_path,
        [_application(url, automation=AutomationStatus.needs_review)],
        [_attempt(url, browser_action_started=True, browser_action_id="old-id")],
    )
    repeated = plan_legacy_lifecycle_migration(
        applications_path=tmp_path / "applications.json",
        attempts_path=tmp_path / "attempts.json",
        sqlite_path=None,
    )

    assert plan.canonical_json() == repeated.canonical_json()
    assert plan.excluded_count >= 1


def test_corrupt_sqlite_is_reported_and_never_repaired(tmp_path):
    db_path = tmp_path / "corrupt.sqlite3"
    corrupt_bytes = b"not a sqlite database"
    db_path.write_bytes(corrupt_bytes)
    apps = tmp_path / "applications.json"
    attempts = tmp_path / "attempts.json"
    _write_json(apps, [])
    _write_json(attempts, [])
    kwargs = {
        "applications_path": apps,
        "attempts_path": attempts,
        "sqlite_path": db_path,
    }

    first = plan_legacy_lifecycle_migration(**kwargs)
    second = plan_legacy_lifecycle_migration(**kwargs)

    assert first.canonical_json() == second.canonical_json()
    assert {item.category: item.state for item in first.sources}["sqlite_completions"] == SourceState.corrupt
    assert any("sqlite_source_unreadable" in item.reason_codes for item in first.records)
    assert db_path.read_bytes() == corrupt_bytes


def test_planner_reports_incomplete_schema_v2_as_corrupt_without_repair(tmp_path):
    db_path = tmp_path / "incomplete-v2.sqlite3"
    SQLiteSubmissionState(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE lifecycle_events")
        connection.commit()
    damaged_bytes = db_path.read_bytes()
    apps = tmp_path / "applications.json"
    attempts = tmp_path / "attempts.json"
    _write_json(apps, [])
    _write_json(attempts, [])

    plan = plan_legacy_lifecycle_migration(
        applications_path=apps,
        attempts_path=attempts,
        sqlite_path=db_path,
    )

    sqlite_source = next(item for item in plan.sources if item.category == "sqlite_completions")
    assert sqlite_source.state == SourceState.corrupt
    assert "sqlite_source_unreadable" in {
        reason for record in plan.records for reason in record.reason_codes
    }
    assert db_path.read_bytes() == damaged_bytes


def test_planner_accepts_valid_schema_v1_read_only(tmp_path):
    db_path = tmp_path / "valid-v1.sqlite3"
    SQLiteSubmissionState(db_path)
    with sqlite3.connect(db_path) as connection:
        for name in SQLiteSubmissionState._V2_TRIGGER_NAMES:
            connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        for table in (
            "lifecycle_events", "lifecycle_attempts", "lifecycle_applications",
            "migration_quarantines", "migration_units", "migration_runs",
        ):
            connection.execute(f'DROP TABLE IF EXISTS "{table}"')
        connection.execute("DROP INDEX IF EXISTS idx_lifecycle_attempt_app")
        connection.execute("DROP INDEX IF EXISTS idx_lifecycle_one_uncertain_attempt")
        connection.execute("DROP INDEX IF EXISTS idx_migration_unit_run")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    v1_bytes = db_path.read_bytes()
    apps = tmp_path / "applications.json"
    attempts = tmp_path / "attempts.json"
    _write_json(apps, [])
    _write_json(attempts, [])

    plan = plan_legacy_lifecycle_migration(
        applications_path=apps,
        attempts_path=attempts,
        sqlite_path=db_path,
    )

    source = next(item for item in plan.sources if item.category == "sqlite_completions")
    assert source.state == SourceState.present
    assert db_path.read_bytes() == v1_bytes


