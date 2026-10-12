import multiprocessing
import os
import sqlite3
from datetime import date

import pytest

from src.applications.evidence import (
    BrowserObservation,
    EvidenceObserver,
    VerifiedSubmissionEvidence,
)
from src.applications.identity import application_id_for
from src.applications.models import ApplicationStatus, AutomationStatus
from src.applications.persistence import SQLiteSubmissionState
from src.applications.recovery import AttemptStore
from src.applications.tracker import ApplicationTracker, load_applications


def _pending_completion(tmp_path, *, busy_timeout_ms=5000):
    app_path = tmp_path / "applications.json"
    tracker = ApplicationTracker(app_path, busy_timeout_ms=busy_timeout_ms)
    job_url = "https://example.test/job/transaction"
    application = tracker.create("Fixture Company", "Engineer", job_url)
    tracker.update_status(job_url, ApplicationStatus.ready)
    tracker.update_automation_status(job_url, AutomationStatus.preparing)
    tracker.update_automation_status(job_url, AutomationStatus.ready_to_submit)
    tracker.update_automation_status(job_url, AutomationStatus.submitting)

    attempts = AttemptStore(
        tmp_path / "attempts.json", allow_test_fixture_evidence=True
    )
    attempt = attempts.claim_submission(
        application_id=application_id_for(job_url),
        company=application.company,
        job_title=application.job_title,
        job_url=job_url,
        platform="local_fixture",
        payload_fingerprint="fixture-payload-v1",
    )
    action_id = attempts.begin_browser_submission_action(
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
    )
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="fixture-observation-transaction",
        browser_action_id=action_id,
        observed_url="file:///fixture/confirmation.html",
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        action_started=True,
        page_changed_after_action=True,
    )
    assert attempts.record_browser_observation(observation)
    evidence = VerifiedSubmissionEvidence(
        mechanism="local_fixture",
        evidence="Fixture confirmation",
        application_id=attempt.application_id,
        job_url=job_url,
        attempt_number=attempt.attempt_number,
        payload_fingerprint=attempt.payload_fingerprint,
        trusted_confirmation_url=observation.observed_url,
        observation=observation,
    )
    assert attempts.record_verified_evidence(attempt, evidence)
    return tracker, attempts, attempts.latest_for(attempt.application_id), evidence, job_url


def _sqlite_completion_worker(path, args, barrier, result_queue):
    try:
        state = SQLiteSubmissionState(path)
        barrier.wait(timeout=30)
        _, created = state.commit_completion(**args)
        result_queue.put(("ok", created))
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))


def _exit_before_completion_commit(path, args, event):
    state = SQLiteSubmissionState(path)

    def exit_with_open_transaction(connection):
        event.set()
        os._exit(31)

    state._before_commit = exit_with_open_transaction
    state.commit_completion(**args)


def _completion_args(attempt, evidence):
    return {
        "application_id": attempt.application_id,
        "attempt_number": attempt.attempt_number,
        "job_url": attempt.job_url,
        "payload_fingerprint": attempt.payload_fingerprint,
        "browser_action_id": evidence.observation.browser_action_id,
        "evidence": evidence,
        "applied_date": date(2026, 10, 12),
    }


def test_verified_completion_and_evidence_consumption_commit_together(tmp_path):
    tracker, attempts, attempt, evidence, job_url = _pending_completion(tmp_path)

    completed = tracker.mark_submitted(
        job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
    )
    record = tracker.submission_state.get_for_application(attempt.application_id)

    assert record is not None
    assert record.evidence == evidence
    assert record.evidence_id == tracker.submission_state.evidence_id(evidence)
    assert record.attempt_id == f"{attempt.application_id}:{attempt.attempt_number}"
    assert record.application_status == ApplicationStatus.applied
    assert record.automation_status == AutomationStatus.submitted
    assert record.payload_fingerprint == attempt.payload_fingerprint
    assert completed.status == ApplicationStatus.applied
    assert completed.automation_status == AutomationStatus.submitted
    stored_attempt = attempts.latest_for(attempt.application_id)
    assert stored_attempt.verified_evidence_consumed is True
    assert stored_attempt.outcome.value == "submitted"

    reopened = ApplicationTracker(tracker.path)
    assert reopened.get(job_url).status == ApplicationStatus.applied
    assert load_applications(tracker.path)[0].status == ApplicationStatus.applied


def test_failure_before_sqlite_commit_leaves_no_partial_completion(
    tmp_path, monkeypatch
):
    tracker, attempts, attempt, evidence, job_url = _pending_completion(tmp_path)

    def interrupt(connection):
        raise OSError("injected before SQLite COMMIT")

    monkeypatch.setattr(tracker.submission_state, "_before_commit", interrupt)
    with pytest.raises(OSError, match="before SQLite COMMIT"):
        tracker.mark_submitted(
            job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
        )

    assert tracker.submission_state.get_for_application(attempt.application_id) is None
    stored_attempt = attempts.latest_for(attempt.application_id)
    assert stored_attempt.verified_evidence_consumed is False
    assert stored_attempt.outcome.value == "submitting"
    assert load_applications(tracker.path)[0].status == ApplicationStatus.ready


def test_process_exit_during_sqlite_transaction_rolls_back(tmp_path):
    tracker, _, attempt, evidence, _ = _pending_completion(tmp_path)
    context = multiprocessing.get_context("spawn")
    event = context.Event()
    args = _completion_args(attempt, evidence)
    process = context.Process(
        target=_exit_before_completion_commit,
        args=(str(tracker.persistence_path), args, event),
    )
    process.start()
    try:
        assert event.wait(timeout=30)
        process.join(timeout=30)
        assert process.exitcode == 31
        reopened = SQLiteSubmissionState(tracker.persistence_path)
        assert reopened.get_for_application(attempt.application_id) is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def test_two_windows_processes_commit_one_evidence_record(tmp_path):
    tracker, _, attempt, evidence, _ = _pending_completion(tmp_path)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    args = _completion_args(attempt, evidence)
    processes = [
        context.Process(
            target=_sqlite_completion_worker,
            args=(str(tracker.persistence_path), args, barrier, result_queue),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        results = [result_queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
        assert all(process.exitcode == 0 for process in processes)
        assert all(result[0] == "ok" for result in results), results
        assert sum(result[1] for result in results) == 1
        assert len(SQLiteSubmissionState(tracker.persistence_path).list_completions()) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()


def test_sqlite_busy_timeout_fails_closed_without_consuming_evidence(tmp_path):
    tracker, attempts, attempt, evidence, job_url = _pending_completion(
        tmp_path, busy_timeout_ms=75
    )
    connection = sqlite3.connect(tracker.persistence_path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            tracker.mark_submitted(
                job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
            )
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()

    assert tracker.submission_state.get_for_application(attempt.application_id) is None
    assert attempts.latest_for(attempt.application_id).verified_evidence_consumed is False


def test_existing_completion_is_idempotent_but_conflicts_are_rejected(tmp_path):
    tracker, attempts, attempt, evidence, job_url = _pending_completion(tmp_path)
    first = tracker.mark_submitted(
        job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
    )
    replay = tracker.mark_submitted(
        job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
    )
    assert replay == first

    altered = evidence.model_copy(update={"payload_fingerprint": "other-payload"})
    with pytest.raises(ValueError):
        tracker.mark_submitted(
            job_url, verified_evidence=altered, attempt=attempt, attempts=attempts
        )
    replay_attempt = attempt.model_copy(
        update={"attempt_number": attempt.attempt_number + 1}
    )
    with pytest.raises(ValueError):
        tracker.mark_submitted(
            job_url,
            verified_evidence=evidence,
            attempt=replay_attempt,
            attempts=attempts,
        )
    assert len(tracker.submission_state.list_completions()) == 1


def test_legacy_json_is_not_imported_as_trusted_completion(tmp_path):
    tracker, attempts, attempt, evidence, job_url = _pending_completion(tmp_path)
    attempts.update(
        attempt,
        verified_evidence_consumed=True,
    )

    assert tracker.submission_state.get_for_application(attempt.application_id) is None
    assert tracker.recover_consumed_submission(job_url, attempts=attempts) is None
    assert tracker.get(job_url).status == ApplicationStatus.ready
    assert tracker.get(job_url).automation_status == AutomationStatus.submitting
    assert evidence.evidence == "Fixture confirmation"


def test_corrupt_sqlite_state_fails_closed_without_importing_json(tmp_path):
    app_path = tmp_path / "applications.json"
    ApplicationTracker(app_path)
    db_path = tmp_path / "broken.sqlite3"
    db_path.write_text("not a sqlite database", encoding="utf-8")

    with pytest.raises(sqlite3.DatabaseError):
        ApplicationTracker(app_path, persistence_path=db_path)


def test_mismatched_evidence_inside_sqlite_completion_fails_closed(tmp_path):
    tracker, attempts, attempt, evidence, job_url = _pending_completion(tmp_path)
    tracker.mark_submitted(
        job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
    )
    tampered_evidence = evidence.model_copy(
        update={"payload_fingerprint": "other-payload"}
    )
    connection = sqlite3.connect(tracker.persistence_path)
    try:
        connection.execute(
            "UPDATE submission_completions SET evidence_json = ? WHERE application_id = ?",
            (tampered_evidence.model_dump_json(), attempt.application_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="Corrupt or mismatched"):
        ApplicationTracker(tracker.path).get(job_url)


def _schema_connection(state):
    connection = sqlite3.connect(state.path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _insert_migration_run(connection, migration_id="migration-1"):
    connection.execute(
        """INSERT INTO migration_runs (
            migration_id, plan_digest, source_manifest_json, target_schema_version,
            state, created_at, updated_at
        ) VALUES (?, 'plan-digest', '{}', 2, 'running', 'now', 'now')""",
        (migration_id,),
    )


def _insert_migration_unit(
    connection, unit_id, *, migration_id="migration-1", source_category="application",
    disposition="imported", reason="history_imported", target_application_id=None,
    target_attempt_id=None,
):
    if disposition == "imported" and target_application_id is None and target_attempt_id is None:
        if source_category == "application":
            target_application_id = unit_id
        elif source_category == "attempt":
            target_attempt_id = unit_id
    connection.execute(
        """INSERT INTO migration_units (
            unit_id, migration_id, source_category, source_path, source_sha256,
            row_number, record_id, disposition, reason_code, target_application_id,
            target_attempt_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'now')""",
        (unit_id, migration_id, source_category, f"{unit_id}.json", "a" * 64,
         1, unit_id, disposition, reason, target_application_id, target_attempt_id),
    )


def _insert_runtime_application(connection, *, application_id="app-1", url="https://example.test/one"):
    connection.execute(
        """INSERT INTO lifecycle_applications (
            application_id, normalized_job_url, job_url, company, job_title,
            business_status, automation_status, applied_date, record_origin,
            source_unit_id, created_at, updated_at
        ) VALUES (?, ?, ?, 'Fixture Co', 'Engineer', 'discovered', 'not_started',
                  NULL, 'runtime', NULL, 'now', 'now')""",
        (application_id, url, url),
    )


def test_fresh_database_creates_v2_with_separate_history_and_completion_tables(tmp_path):
    state = SQLiteSubmissionState(tmp_path / "fresh.sqlite3")
    connection = _schema_connection(state)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "submission_completions",
            "lifecycle_applications",
            "lifecycle_attempts",
            "lifecycle_events",
            "migration_runs",
            "migration_units",
            "migration_quarantines",
        }.issubset(tables)
        assert not {"submission_authorizations", "browser_observations", "verified_evidence"}.intersection(tables)
    finally:
        connection.close()


def test_schema_v1_upgrade_preserves_existing_completion_rows(tmp_path):
    tracker, attempts, attempt, evidence, job_url = _pending_completion(tmp_path)
    tracker.mark_submitted(
        job_url, verified_evidence=evidence, attempt=attempt, attempts=attempts
    )
    db_path = tracker.persistence_path
    connection = sqlite3.connect(db_path, isolation_level=None)
    try:
        before = connection.execute(
            "SELECT * FROM submission_completions ORDER BY application_id"
        ).fetchall()
        for name in SQLiteSubmissionState._V2_TRIGGER_NAMES:
            connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        for table in (
            "lifecycle_events", "lifecycle_attempts", "lifecycle_applications",
            "migration_quarantines", "migration_units", "migration_runs",
        ):
            connection.execute(f'DROP TABLE IF EXISTS "{table}"')
        connection.execute("DROP INDEX IF EXISTS idx_lifecycle_attempt_app")
        connection.execute("DROP INDEX IF EXISTS idx_migration_unit_run")
        connection.execute("PRAGMA user_version = 1")
    finally:
        connection.close()

    upgraded = SQLiteSubmissionState(db_path)
    connection = _schema_connection(upgraded)
    try:
        after = connection.execute(
            "SELECT * FROM submission_completions ORDER BY application_id"
        ).fetchall()
        assert before == after
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert upgraded.get_for_application(attempt.application_id).evidence == evidence
    finally:
        connection.close()


def test_schema_initialization_is_idempotent_and_rejects_future_version(tmp_path):
    path = tmp_path / "idempotent.sqlite3"
    for _ in range(3):
        SQLiteSubmissionState(path)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        connection.execute("PRAGMA user_version = 99")
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="Unsupported.*schema version: 99"):
        SQLiteSubmissionState(path)


@pytest.mark.parametrize("version,objects", [(1, False), (0, True), (2, False)])
def test_malformed_schema_metadata_fails_closed(tmp_path, version, objects):
    path = tmp_path / f"malformed-{version}-{objects}.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        if objects:
            connection.execute("CREATE TABLE unexpected (value TEXT)")
        connection.execute(f"PRAGMA user_version = {version}")
    finally:
        connection.close()
    with pytest.raises(RuntimeError):
        SQLiteSubmissionState(path)


def test_corrupt_database_fails_closed_during_schema_initialization(tmp_path):
    path = tmp_path / "corrupt-schema.sqlite3"
    path.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteSubmissionState(path)
    assert path.read_bytes() == b"not a sqlite database"


def test_altered_schema_trigger_fails_closed(tmp_path):
    path = tmp_path / "altered-trigger.sqlite3"
    state = SQLiteSubmissionState(path)
    connection = _schema_connection(state)
    try:
        connection.execute("DROP TRIGGER lifecycle_application_insert_guard")
        connection.execute(
            "CREATE TRIGGER lifecycle_application_insert_guard BEFORE INSERT ON lifecycle_applications BEGIN SELECT 1; END"
        )
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="Malformed schema-v2 SQL metadata"):
        SQLiteSubmissionState(path)


def test_unversioned_extra_schema_object_fails_closed(tmp_path):
    path = tmp_path / "extra-object.sqlite3"
    state = SQLiteSubmissionState(path)
    connection = _schema_connection(state)
    try:
        connection.execute("CREATE TABLE unexpected_v2_object (value TEXT)")
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="Malformed schema-v2 SQL metadata"):
        SQLiteSubmissionState(path)


def test_lifecycle_constraints_transitions_and_audit_events(tmp_path):
    state = SQLiteSubmissionState(tmp_path / "constraints.sqlite3")
    connection = _schema_connection(state)
    try:
        _insert_runtime_application(connection)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _insert_runtime_application(connection, application_id="app-duplicate")
        connection.execute(
            "UPDATE lifecycle_applications SET business_status = 'ready' WHERE application_id = 'app-1'"
        )
        connection.execute(
            "UPDATE lifecycle_applications SET automation_status = 'preparing' WHERE application_id = 'app-1'"
        )
        connection.execute(
            "UPDATE lifecycle_applications SET automation_status = 'ready_to_submit' WHERE application_id = 'app-1'"
        )
        connection.execute(
            "UPDATE lifecycle_applications SET automation_status = 'submitting' WHERE application_id = 'app-1'"
        )
        events = connection.execute(
            "SELECT state_kind, from_state, to_state FROM lifecycle_events ORDER BY event_id"
        ).fetchall()
        assert events == [
            ("business", "discovered", "ready"),
            ("automation", "not_started", "preparing"),
            ("automation", "preparing", "ready_to_submit"),
            ("automation", "ready_to_submit", "submitting"),
        ]
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            connection.execute(
                "UPDATE lifecycle_applications SET normalized_job_url = 'https://example.test/other' WHERE application_id = 'app-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="illegal business status transition"):
            connection.execute(
                "UPDATE lifecycle_applications SET business_status = 'offer' WHERE application_id = 'app-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="trusted completion required"):
            connection.execute(
                "UPDATE lifecycle_applications SET business_status = 'applied', automation_status = 'submitted' WHERE application_id = 'app-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM lifecycle_events")
    finally:
        connection.close()


def test_migration_units_and_imported_history_are_atomic_and_deduplicated(tmp_path):
    state = SQLiteSubmissionState(tmp_path / "migration-units.sqlite3")
    connection = _schema_connection(state)
    try:
        _insert_migration_run(connection)
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            connection.execute(
                "UPDATE migration_runs SET plan_digest = 'changed' WHERE migration_id = 'migration-1'"
            )
        connection.execute("BEGIN IMMEDIATE")
        _insert_migration_unit(
            connection, "unit-rollback", target_application_id="bad-app"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO lifecycle_applications (
                    application_id, normalized_job_url, job_url, company, job_title,
                    business_status, automation_status, record_origin, source_unit_id,
                    created_at, updated_at
                ) VALUES ('bad-app', 'https://example.test/bad', 'https://example.test/bad',
                          'Fixture Co', 'Engineer', 'applied', 'submitting',
                          'legacy_json', 'unit-rollback', 'now', 'now')"""
            )
        connection.rollback()
        assert connection.execute(
            "SELECT COUNT(*) FROM migration_units WHERE unit_id = 'unit-rollback'"
        ).fetchone()[0] == 0

        connection.execute("BEGIN IMMEDIATE")
        _insert_migration_unit(
            connection, "unit-app", target_application_id="app-imported"
        )
        connection.execute(
            """INSERT INTO lifecycle_applications (
                application_id, normalized_job_url, job_url, company, job_title,
                business_status, automation_status, record_origin, source_unit_id,
                created_at, updated_at
            ) VALUES ('app-imported', 'https://example.test/imported',
                      'https://example.test/imported', 'Fixture Co', 'Engineer',
                      'ready', 'needs_review', 'legacy_json', 'unit-app', 'now', 'now')"""
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_migration_unit(connection, "unit-app")

        _insert_migration_unit(
            connection, "unit-quarantine", disposition="quarantined", reason="duplicate_identity"
        )
        quarantine_count = connection.execute(
            "SELECT COUNT(*) FROM migration_quarantines WHERE unit_id = 'unit-quarantine'"
        ).fetchone()[0]
        assert quarantine_count == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_applications WHERE application_id = 'app-imported'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM submission_completions").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO migration_quarantines(unit_id, details_json, created_at) VALUES ('unit-app', '{}', 'now')"
            )
        connection.execute("BEGIN IMMEDIATE")
        _insert_migration_unit(
            connection,
            "unit-attempt",
            source_category="attempt",
            target_attempt_id="app-imported:1",
        )
        connection.execute(
            """INSERT INTO lifecycle_attempts (
                attempt_id, application_id, normalized_job_url, attempt_number,
                job_url, platform, payload_fingerprint, outcome, submission_outcome,
                retryable, record_origin, source_unit_id, started_at
            ) VALUES ('app-imported:1', 'app-imported', 'https://example.test/imported',
                      1, 'https://example.test/imported', 'fixture', 'payload',
                      'unknown_submission_result', 'unknown', 0, 'legacy_json',
                      'unit-attempt', 'now')"""
        )
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        _insert_migration_unit(
            connection,
            "unit-attempt-retry",
            source_category="attempt",
            target_attempt_id="app-imported:2",
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO lifecycle_attempts (
                    attempt_id, application_id, normalized_job_url, attempt_number,
                    job_url, platform, payload_fingerprint, outcome, submission_outcome,
                    retryable, record_origin, source_unit_id, started_at
                ) VALUES ('app-imported:2', 'app-imported', 'https://example.test/imported',
                          2, 'https://example.test/imported', 'fixture', 'payload',
                          'unknown_submission_result', 'unknown', 1, 'legacy_json',
                          'unit-attempt-retry', 'now')"""
            )
        connection.rollback()
        connection.execute(
            "UPDATE migration_runs SET state = 'completed', updated_at = 'later' WHERE migration_id = 'migration-1'"
        )
        with pytest.raises(sqlite3.IntegrityError, match="not active"):
            _insert_migration_unit(connection, "unit-after-complete")
    finally:
        connection.close()


def test_unknown_attempts_cannot_be_retryable_or_claim_completion(tmp_path):
    state = SQLiteSubmissionState(tmp_path / "unknown-schema.sqlite3")
    connection = _schema_connection(state)
    try:
        _insert_runtime_application(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO lifecycle_attempts (
                    attempt_id, application_id, normalized_job_url, attempt_number, job_url, platform,
                    payload_fingerprint, outcome, submission_outcome, retryable,
                    record_origin, started_at
                ) VALUES ('app-1:1', 'app-1', 'https://example.test/one', 1, 'https://example.test/one',
                          'fixture', 'payload', 'unknown_submission_result', 'unknown',
                          1, 'runtime', 'now')"""
            )
        connection.execute(
            """INSERT INTO lifecycle_attempts (
                attempt_id, application_id, normalized_job_url, attempt_number, job_url, platform,
                payload_fingerprint, outcome, submission_outcome, retryable,
                record_origin, started_at
            ) VALUES ('app-1:1', 'app-1', 'https://example.test/one', 1, 'https://example.test/one',
                      'fixture', 'payload', 'unknown_submission_result', 'unknown',
                      0, 'runtime', 'now')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="attempt identity is immutable"):
            connection.execute(
                "UPDATE lifecycle_attempts SET payload_fingerprint = 'changed' WHERE attempt_id = 'app-1:1'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE lifecycle_attempts SET outcome = 'submitted', submission_outcome = 'submitted' WHERE attempt_id = 'app-1:1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="terminal attempt outcome"):
            connection.execute(
                "UPDATE lifecycle_attempts SET outcome = 'failed', submission_outcome = 'not_submitted' WHERE attempt_id = 'app-1:1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: lifecycle_attempts.application_id"):
            connection.execute(
                """INSERT INTO lifecycle_attempts (
                    attempt_id, application_id, normalized_job_url, attempt_number,
                    job_url, platform, payload_fingerprint, outcome,
                    submission_outcome, retryable, record_origin, started_at
                ) VALUES ('app-1:2', 'app-1', 'https://example.test/one', 2,
                          'https://example.test/one', 'fixture', 'payload',
                          'submitting', 'not_started', 0, 'runtime', 'now')"""
            )
        connection.execute(
            """INSERT INTO lifecycle_attempts (
                attempt_id, application_id, normalized_job_url, attempt_number,
                job_url, platform, payload_fingerprint, outcome, submission_outcome,
                retryable, record_origin, started_at
            ) VALUES ('app-1:2', 'app-1', 'https://example.test/one', 2,
                      'https://example.test/one', 'fixture', 'payload',
                      'failed', 'not_submitted', 0, 'runtime', 'now')"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(
                """INSERT INTO lifecycle_attempts (
                    attempt_id, application_id, normalized_job_url, attempt_number,
                    job_url, platform, payload_fingerprint, outcome, submission_outcome,
                    retryable, record_origin, started_at
                ) VALUES ('app-1:2', 'app-1', 'https://example.test/one', 2,
                          'https://example.test/one', 'fixture', 'payload',
                          'failed', 'not_submitted', 0, 'runtime', 'now')"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO lifecycle_attempts (
                    attempt_id, application_id, normalized_job_url, attempt_number,
                    job_url, platform, payload_fingerprint, outcome,
                    submission_outcome, retryable, record_origin, started_at
                ) VALUES ('app-1:2', 'app-1', 'https://example.test/other', 2,
                          'https://example.test/other', 'fixture', 'payload',
                          'failed', 'not_submitted', 0, 'runtime', 'now')"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO lifecycle_attempts (
                    attempt_id, application_id, normalized_job_url, attempt_number, job_url, platform,
                    payload_fingerprint, outcome, submission_outcome, retryable,
                    record_origin, started_at
                ) VALUES ('app-1:2', 'missing-app', 'https://example.test/one', 2, 'https://example.test/one',
                          'fixture', 'payload', 'failed', 'not_submitted', 0, 'runtime', 'now')"""
            )
    finally:
        connection.close()
