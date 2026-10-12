from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from src.applications.evidence import VerifiedSubmissionEvidence
from src.applications.models import ApplicationStatus, AutomationStatus
from src.research.job_normalizer import normalize_job_url


class SubmissionCompletion(BaseModel):
    application_id: str
    attempt_id: str
    attempt_number: int = Field(ge=1)
    job_url: str
    normalized_job_url: str
    payload_fingerprint: str
    browser_action_id: str
    evidence_id: str
    evidence: VerifiedSubmissionEvidence
    application_status: ApplicationStatus = ApplicationStatus.applied
    automation_status: AutomationStatus = AutomationStatus.submitted
    applied_date: date
    completed_at: datetime


class SQLiteSubmissionState:
    SCHEMA_VERSION = 2

    _COMPLETION_COLUMNS = (
        "application_id", "attempt_id", "attempt_number", "job_url",
        "normalized_job_url", "payload_fingerprint", "browser_action_id",
        "evidence_id", "evidence_json", "attempt_outcome",
        "submission_outcome", "application_status", "automation_status",
        "applied_date", "completed_at",
    )
    _V2_COLUMNS = {
        "migration_runs": (
            "migration_id", "plan_digest", "source_manifest_json",
            "target_schema_version", "state", "created_at", "updated_at",
        ),
        "migration_units": (
            "unit_id", "migration_id", "source_category", "source_path",
            "source_sha256", "row_number", "record_id", "disposition",
            "reason_code", "target_application_id", "target_attempt_id", "created_at",
        ),
        "migration_quarantines": (
            "unit_id", "details_json", "created_at",
        ),
        "lifecycle_applications": (
            "application_id", "normalized_job_url", "job_url", "company",
            "job_title", "business_status", "automation_status", "applied_date",
            "record_origin", "source_unit_id", "created_at", "updated_at",
        ),
        "lifecycle_attempts": (
            "attempt_id", "application_id", "normalized_job_url", "attempt_number", "job_url",
            "platform", "payload_fingerprint", "outcome", "submission_outcome",
            "retryable", "record_origin", "source_unit_id", "started_at",
            "completed_at",
        ),
        "lifecycle_events": (
            "event_id", "application_id", "attempt_id", "state_kind",
            "from_state", "to_state", "event_kind", "created_at",
        ),
    }
    _V2_TRIGGER_NAMES = {
        "lifecycle_application_insert_guard",
        "lifecycle_application_business_transition_guard",
        "lifecycle_application_automation_transition_guard",
        "lifecycle_application_business_event",
        "lifecycle_application_automation_event",
        "lifecycle_attempt_insert_guard",
        "lifecycle_attempt_update_guard",
        "lifecycle_attempt_identity_guard",
        "lifecycle_attempt_terminal_guard",
        "lifecycle_application_identity_guard",
        "migration_quarantine_create",
        "migration_quarantine_guard",
        "migration_run_identity_guard",
        "migration_run_state_guard",
        "migration_unit_run_guard",
        "migration_application_import_guard",
        "migration_attempt_import_guard",
        "migration_unit_update_guard",
        "migration_unit_delete_guard",
        "migration_quarantine_update_guard",
        "migration_quarantine_delete_guard",
        "lifecycle_event_update_guard",
        "lifecycle_event_delete_guard",
    }

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def evidence_id(evidence: VerifiedSubmissionEvidence) -> str:
        canonical = json.dumps(
            evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None
        )
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1, self.SCHEMA_VERSION}:
                raise RuntimeError(f"Unsupported submission-state schema version: {version}")

            if version == 0:
                objects = connection.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type IN ('table', 'view', 'trigger')
                         AND name NOT LIKE 'sqlite_%'"""
                ).fetchall()
                if objects:
                    raise RuntimeError(
                        "Schema version 0 has unexpected application objects"
                    )
                for statement in self._v1_statements():
                    connection.execute(statement)
            else:
                if version == 1:
                    self._validate_v1_schema(connection)
                else:
                    self._validate_schema(connection, version)
                self._validate_completion_rows(connection)

            if version < 2:
                for statement in self._v2_statements():
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 2")

            self._validate_schema(connection, 2)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _v1_statements() -> tuple[str, ...]:
        return (
            """CREATE TABLE submission_completions (
                    application_id TEXT NOT NULL PRIMARY KEY,
                    attempt_id TEXT NOT NULL UNIQUE,
                    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                    job_url TEXT NOT NULL,
                    normalized_job_url TEXT NOT NULL UNIQUE,
                    payload_fingerprint TEXT NOT NULL,
                    browser_action_id TEXT NOT NULL UNIQUE,
                    evidence_id TEXT NOT NULL UNIQUE,
                    evidence_json TEXT NOT NULL,
                    attempt_outcome TEXT NOT NULL CHECK (attempt_outcome = 'submitted'),
                    submission_outcome TEXT NOT NULL CHECK (submission_outcome = 'submitted'),
                    application_status TEXT NOT NULL CHECK (application_status = 'applied'),
                    automation_status TEXT NOT NULL CHECK (automation_status = 'submitted'),
                    applied_date TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    UNIQUE (application_id, attempt_number)
                )""",
            "CREATE INDEX idx_submission_payload ON submission_completions(payload_fingerprint)",
            "CREATE INDEX idx_submission_attempt ON submission_completions(attempt_number)",
            "CREATE INDEX idx_submission_completion_status ON submission_completions(application_status, automation_status)",
            )

    @staticmethod
    def _v2_statements() -> tuple[str, ...]:
        return (
            """CREATE TABLE migration_runs (
                migration_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(migration_id)) > 0),
                plan_digest TEXT NOT NULL CHECK (length(trim(plan_digest)) > 0),
                source_manifest_json TEXT NOT NULL,
                target_schema_version INTEGER NOT NULL CHECK (target_schema_version = 2),
                state TEXT NOT NULL CHECK (state IN ('running', 'completed', 'failed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE migration_units (
                unit_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(unit_id)) > 0),
                migration_id TEXT NOT NULL REFERENCES migration_runs(migration_id) ON DELETE RESTRICT,
                source_category TEXT NOT NULL CHECK (length(trim(source_category)) > 0),
                source_path TEXT NOT NULL CHECK (length(trim(source_path)) > 0),
                source_sha256 TEXT NOT NULL CHECK (
                    length(source_sha256) = 64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'
                ),
                row_number INTEGER NOT NULL CHECK (row_number > 0),
                record_id TEXT NOT NULL CHECK (length(trim(record_id)) > 0),
                disposition TEXT NOT NULL CHECK (disposition IN ('imported', 'quarantined', 'excluded')),
                reason_code TEXT NOT NULL CHECK (length(trim(reason_code)) > 0),
                target_application_id TEXT,
                target_attempt_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (migration_id, source_category, source_path, source_sha256, row_number),
                UNIQUE (migration_id, record_id),
                CHECK ((disposition = 'imported' AND
                        ((target_application_id IS NOT NULL AND target_attempt_id IS NULL) OR
                         (target_application_id IS NULL AND target_attempt_id IS NOT NULL))) OR
                       (disposition != 'imported' AND target_application_id IS NULL AND target_attempt_id IS NULL)),
                FOREIGN KEY (target_application_id, unit_id)
                    REFERENCES lifecycle_applications(application_id, source_unit_id)
                    ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
                FOREIGN KEY (target_attempt_id, unit_id)
                    REFERENCES lifecycle_attempts(attempt_id, source_unit_id)
                    ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
            )""",
            """CREATE TABLE migration_quarantines (
                unit_id TEXT NOT NULL PRIMARY KEY REFERENCES migration_units(unit_id) ON DELETE RESTRICT,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE lifecycle_applications (
                application_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(application_id)) > 0),
                normalized_job_url TEXT NOT NULL UNIQUE CHECK (length(trim(normalized_job_url)) > 0),
                job_url TEXT NOT NULL CHECK (length(trim(job_url)) > 0),
                company TEXT NOT NULL CHECK (length(trim(company)) > 0),
                job_title TEXT NOT NULL CHECK (length(trim(job_title)) > 0),
                business_status TEXT NOT NULL CHECK (business_status IN ('discovered', 'ready', 'applied', 'rejected', 'interview', 'offer', 'withdrawn')),
                automation_status TEXT NOT NULL CHECK (automation_status IN ('not_started', 'preparing', 'needs_review', 'ready_to_submit', 'submitting', 'submitted', 'failed', 'unsupported', 'human_action_required', 'unknown_submission_result')),
                applied_date TEXT,
                record_origin TEXT NOT NULL CHECK (record_origin IN ('legacy_json', 'runtime')),
                source_unit_id TEXT UNIQUE REFERENCES migration_units(unit_id)
                    ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (application_id, normalized_job_url),
                CHECK ((record_origin = 'legacy_json' AND source_unit_id IS NOT NULL) OR
                       (record_origin = 'runtime' AND source_unit_id IS NULL)),
                CHECK (business_status != 'applied' OR automation_status = 'submitted'),
                CHECK (record_origin != 'legacy_json' OR automation_status != 'submitting'),
                UNIQUE (application_id, source_unit_id)
            )""",
            """CREATE TABLE lifecycle_attempts (
                attempt_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(attempt_id)) > 0),
                application_id TEXT NOT NULL REFERENCES lifecycle_applications(application_id) ON DELETE RESTRICT,
                normalized_job_url TEXT NOT NULL CHECK (length(trim(normalized_job_url)) > 0),
                attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                job_url TEXT NOT NULL CHECK (length(trim(job_url)) > 0),
                platform TEXT NOT NULL CHECK (length(trim(platform)) > 0),
                payload_fingerprint TEXT,
                outcome TEXT NOT NULL CHECK (outcome IN ('started', 'needs_review', 'submitting', 'submitted', 'failed', 'unsupported', 'human_action_required', 'unknown_submission_result')),
                submission_outcome TEXT NOT NULL CHECK (submission_outcome IN ('not_started', 'not_submitted', 'submitted', 'unknown')),
                retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
                record_origin TEXT NOT NULL CHECK (record_origin IN ('legacy_json', 'runtime')),
                source_unit_id TEXT UNIQUE REFERENCES migration_units(unit_id)
                    ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE (application_id, attempt_number),
                FOREIGN KEY (application_id, normalized_job_url)
                    REFERENCES lifecycle_applications(application_id, normalized_job_url)
                    ON DELETE RESTRICT,
                CHECK (attempt_id = application_id || ':' || attempt_number),
                CHECK ((record_origin = 'legacy_json' AND source_unit_id IS NOT NULL AND retryable = 0) OR
                       (record_origin = 'runtime' AND source_unit_id IS NULL)),
                CHECK (outcome != 'unknown_submission_result' OR
                       (submission_outcome = 'unknown' AND retryable = 0)),
                CHECK (submission_outcome != 'unknown' OR outcome = 'unknown_submission_result'),
                CHECK (outcome != 'submitted' OR submission_outcome = 'submitted'),
                CHECK (submission_outcome != 'submitted' OR outcome = 'submitted'),
                CHECK (record_origin != 'legacy_json' OR outcome != 'submitting'),
                UNIQUE (attempt_id, source_unit_id)
            )""",
            """CREATE TABLE lifecycle_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id TEXT NOT NULL REFERENCES lifecycle_applications(application_id) ON DELETE RESTRICT,
                attempt_id TEXT REFERENCES lifecycle_attempts(attempt_id) ON DELETE RESTRICT,
                state_kind TEXT NOT NULL CHECK (state_kind IN ('business', 'automation')),
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                event_kind TEXT NOT NULL CHECK (event_kind IN ('state_transition')),
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX idx_lifecycle_attempt_app ON lifecycle_attempts(application_id, attempt_number)",
            "CREATE UNIQUE INDEX idx_lifecycle_one_uncertain_attempt ON lifecycle_attempts(application_id) WHERE outcome IN ('submitting', 'unknown_submission_result')",
            "CREATE INDEX idx_migration_unit_run ON migration_units(migration_id, row_number)",
            """CREATE TRIGGER migration_run_identity_guard
                BEFORE UPDATE OF migration_id, plan_digest, source_manifest_json,
                                 target_schema_version, created_at ON migration_runs
                WHEN OLD.migration_id != NEW.migration_id OR
                     OLD.plan_digest != NEW.plan_digest OR
                     OLD.source_manifest_json != NEW.source_manifest_json OR
                     OLD.target_schema_version != NEW.target_schema_version OR
                     OLD.created_at != NEW.created_at
                BEGIN SELECT RAISE(ABORT, 'migration run identity is immutable'); END""",
            """CREATE TRIGGER migration_run_state_guard
                BEFORE UPDATE OF state ON migration_runs
                WHEN OLD.state != NEW.state AND NOT
                     (OLD.state = 'running' AND NEW.state IN ('completed', 'failed'))
                BEGIN SELECT RAISE(ABORT, 'illegal migration run state transition'); END""",
            """CREATE TRIGGER migration_unit_update_guard
                BEFORE UPDATE ON migration_units
                BEGIN SELECT RAISE(ABORT, 'migration units are immutable'); END""",
            """CREATE TRIGGER migration_unit_run_guard
                BEFORE INSERT ON migration_units
                WHEN NOT EXISTS (
                    SELECT 1 FROM migration_runs r
                    WHERE r.migration_id = NEW.migration_id AND r.state = 'running'
                )
                BEGIN SELECT RAISE(ABORT, 'migration run is not active'); END""",
            """CREATE TRIGGER migration_unit_delete_guard
                BEFORE DELETE ON migration_units
                BEGIN SELECT RAISE(ABORT, 'migration units are immutable'); END""",
            """CREATE TRIGGER migration_quarantine_update_guard
                BEFORE UPDATE ON migration_quarantines
                BEGIN SELECT RAISE(ABORT, 'quarantine records are immutable'); END""",
            """CREATE TRIGGER migration_quarantine_delete_guard
                BEFORE DELETE ON migration_quarantines
                BEGIN SELECT RAISE(ABORT, 'quarantine records are immutable'); END""",
            """CREATE TRIGGER migration_quarantine_create
                AFTER INSERT ON migration_units
                WHEN NEW.disposition = 'quarantined'
                BEGIN
                    INSERT INTO migration_quarantines(unit_id, details_json, created_at)
                    VALUES (NEW.unit_id, '{}', NEW.created_at);
                END""",
            """CREATE TRIGGER migration_quarantine_guard
                BEFORE INSERT ON migration_quarantines
                WHEN NOT EXISTS (
                    SELECT 1 FROM migration_units u
                    WHERE u.unit_id = NEW.unit_id AND u.disposition = 'quarantined'
                )
                BEGIN SELECT RAISE(ABORT, 'quarantine metadata requires quarantined migration unit'); END""",
            """CREATE TRIGGER lifecycle_application_insert_guard
                BEFORE INSERT ON lifecycle_applications
                WHEN NEW.business_status = 'applied' OR NEW.automation_status = 'submitted'
                BEGIN
                    SELECT CASE WHEN NOT EXISTS (
                        SELECT 1 FROM submission_completions c
                        WHERE c.application_id = NEW.application_id
                          AND c.normalized_job_url = NEW.normalized_job_url
                    ) THEN RAISE(ABORT, 'trusted completion required') END;
                END""",
            """CREATE TRIGGER migration_application_import_guard
                BEFORE INSERT ON lifecycle_applications
                WHEN NEW.record_origin = 'legacy_json' AND NOT EXISTS (
                    SELECT 1 FROM migration_units u
                    WHERE u.unit_id = NEW.source_unit_id
                      AND u.disposition = 'imported'
                      AND u.source_category = 'application'
                      AND u.target_application_id = NEW.application_id
                      AND u.target_attempt_id IS NULL
                )
                BEGIN SELECT RAISE(ABORT, 'application import requires matching migration unit'); END""",
            """CREATE TRIGGER migration_attempt_import_guard
                BEFORE INSERT ON lifecycle_attempts
                WHEN NEW.record_origin = 'legacy_json' AND NOT EXISTS (
                    SELECT 1 FROM migration_units u
                    WHERE u.unit_id = NEW.source_unit_id
                      AND u.disposition = 'imported'
                      AND u.source_category = 'attempt'
                      AND u.target_attempt_id = NEW.attempt_id
                      AND u.target_application_id IS NULL
                )
                BEGIN SELECT RAISE(ABORT, 'attempt import requires matching migration unit'); END""",
            """CREATE TRIGGER lifecycle_application_identity_guard
                BEFORE UPDATE OF application_id, normalized_job_url, record_origin,
                                 source_unit_id ON lifecycle_applications
                WHEN OLD.application_id != NEW.application_id OR
                     OLD.normalized_job_url != NEW.normalized_job_url OR
                     OLD.record_origin != NEW.record_origin OR
                     OLD.source_unit_id IS NOT NEW.source_unit_id
                BEGIN SELECT RAISE(ABORT, 'application identity is immutable'); END""",
            """CREATE TRIGGER lifecycle_application_business_transition_guard
                BEFORE UPDATE OF business_status ON lifecycle_applications
                WHEN OLD.business_status != NEW.business_status
                BEGIN
                    SELECT CASE WHEN NOT (
                        (OLD.business_status = 'discovered' AND NEW.business_status IN ('ready', 'rejected')) OR
                        (OLD.business_status = 'ready' AND NEW.business_status IN ('applied', 'rejected', 'withdrawn')) OR
                        (OLD.business_status = 'applied' AND NEW.business_status IN ('interview', 'rejected', 'withdrawn')) OR
                        (OLD.business_status = 'interview' AND NEW.business_status IN ('offer', 'rejected', 'withdrawn'))
                    ) THEN RAISE(ABORT, 'illegal business status transition') END;
                    SELECT CASE WHEN NEW.business_status = 'applied' AND
                        (NEW.automation_status != 'submitted' OR NOT EXISTS (
                            SELECT 1 FROM submission_completions c
                            WHERE c.application_id = NEW.application_id
                              AND c.normalized_job_url = NEW.normalized_job_url
                        )) THEN RAISE(ABORT, 'trusted completion required') END;
                END""",
            """CREATE TRIGGER lifecycle_application_automation_transition_guard
                BEFORE UPDATE OF automation_status ON lifecycle_applications
                WHEN OLD.automation_status != NEW.automation_status
                BEGIN
                    SELECT CASE WHEN NOT (
                        (OLD.automation_status = 'not_started' AND NEW.automation_status IN ('preparing', 'unsupported')) OR
                        (OLD.automation_status = 'preparing' AND NEW.automation_status IN ('needs_review', 'ready_to_submit', 'failed', 'unsupported', 'human_action_required', 'unknown_submission_result')) OR
                        (OLD.automation_status = 'needs_review' AND NEW.automation_status IN ('preparing', 'human_action_required')) OR
                        (OLD.automation_status = 'ready_to_submit' AND NEW.automation_status IN ('submitting', 'failed', 'unsupported', 'human_action_required', 'unknown_submission_result')) OR
                        (OLD.automation_status = 'submitting' AND NEW.automation_status IN ('submitted', 'failed', 'human_action_required', 'unknown_submission_result')) OR
                        (OLD.automation_status IN ('failed', 'unsupported', 'human_action_required') AND NEW.automation_status = 'preparing')
                    ) THEN RAISE(ABORT, 'illegal automation status transition') END;
                    SELECT CASE WHEN NEW.automation_status = 'submitted' AND
                        NOT EXISTS (
                            SELECT 1 FROM submission_completions c
                            WHERE c.application_id = NEW.application_id
                              AND c.normalized_job_url = NEW.normalized_job_url
                        ) THEN RAISE(ABORT, 'trusted completion required') END;
                END""",
            """CREATE TRIGGER lifecycle_application_business_event
                AFTER UPDATE OF business_status ON lifecycle_applications
                WHEN OLD.business_status != NEW.business_status
                BEGIN
                    INSERT INTO lifecycle_events(application_id, state_kind, from_state, to_state, event_kind, created_at)
                    VALUES (NEW.application_id, 'business', OLD.business_status, NEW.business_status, 'state_transition', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                END""",
            """CREATE TRIGGER lifecycle_application_automation_event
                AFTER UPDATE OF automation_status ON lifecycle_applications
                WHEN OLD.automation_status != NEW.automation_status
                BEGIN
                    INSERT INTO lifecycle_events(application_id, state_kind, from_state, to_state, event_kind, created_at)
                    VALUES (NEW.application_id, 'automation', OLD.automation_status, NEW.automation_status, 'state_transition', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                END""",
            """CREATE TRIGGER lifecycle_attempt_insert_guard
                BEFORE INSERT ON lifecycle_attempts
                WHEN NEW.outcome = 'submitted'
                BEGIN
                    SELECT CASE WHEN NOT EXISTS (
                        SELECT 1 FROM submission_completions c
                        WHERE c.application_id = NEW.application_id
                          AND c.normalized_job_url = NEW.normalized_job_url
                          AND c.attempt_number = NEW.attempt_number
                          AND c.attempt_id = NEW.attempt_id
                          AND c.payload_fingerprint = NEW.payload_fingerprint
                    ) THEN RAISE(ABORT, 'matching trusted completion required') END;
                END""",
            """CREATE TRIGGER lifecycle_attempt_update_guard
                BEFORE UPDATE OF outcome, submission_outcome, retryable, attempt_id,
                                 attempt_number, payload_fingerprint
                ON lifecycle_attempts
                WHEN NEW.outcome = 'submitted'
                BEGIN
                    SELECT CASE WHEN NOT EXISTS (
                        SELECT 1 FROM submission_completions c
                        WHERE c.application_id = NEW.application_id
                          AND c.normalized_job_url = NEW.normalized_job_url
                          AND c.attempt_number = NEW.attempt_number
                          AND c.attempt_id = NEW.attempt_id
                          AND c.payload_fingerprint = NEW.payload_fingerprint
                    ) THEN RAISE(ABORT, 'matching trusted completion required') END;
                END""",
            """CREATE TRIGGER lifecycle_attempt_identity_guard
                BEFORE UPDATE OF attempt_id, application_id, normalized_job_url,
                                 attempt_number, job_url,
                                 platform, payload_fingerprint, record_origin,
                                 source_unit_id, started_at
                ON lifecycle_attempts
                WHEN OLD.attempt_id != NEW.attempt_id OR
                     OLD.application_id != NEW.application_id OR
                     OLD.normalized_job_url != NEW.normalized_job_url OR
                     OLD.attempt_number != NEW.attempt_number OR
                     OLD.job_url != NEW.job_url OR OLD.platform != NEW.platform OR
                     OLD.payload_fingerprint IS NOT NEW.payload_fingerprint OR
                     OLD.record_origin != NEW.record_origin OR
                     OLD.source_unit_id IS NOT NEW.source_unit_id OR
                     OLD.started_at != NEW.started_at
                BEGIN SELECT RAISE(ABORT, 'attempt identity is immutable'); END""",
            """CREATE TRIGGER lifecycle_attempt_terminal_guard
                BEFORE UPDATE OF outcome ON lifecycle_attempts
                WHEN OLD.outcome IN ('submitted', 'unknown_submission_result')
                     AND NEW.outcome != OLD.outcome
                BEGIN SELECT RAISE(ABORT, 'terminal attempt outcome requires reconciliation'); END""",
            """CREATE TRIGGER lifecycle_event_update_guard
                BEFORE UPDATE ON lifecycle_events
                BEGIN SELECT RAISE(ABORT, 'lifecycle events are immutable'); END""",
            """CREATE TRIGGER lifecycle_event_delete_guard
                BEFORE DELETE ON lifecycle_events
                BEGIN SELECT RAISE(ABORT, 'lifecycle events are immutable'); END""",
        )

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
        return tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))

    @classmethod
    def _validate_completion_schema(cls, connection: sqlite3.Connection) -> None:
        columns = cls._table_columns(connection, "submission_completions")
        if columns != cls._COMPLETION_COLUMNS:
            raise RuntimeError("Missing or malformed submission completion schema")

    @classmethod
    def _validate_v1_schema(cls, connection: sqlite3.Connection) -> None:
        cls._validate_completion_schema(connection)
        expected_sql = {}
        for statement in cls._v1_statements():
            match = re.match(
                r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+([A-Za-z_][A-Za-z0-9_]*)",
                statement.strip(),
                flags=re.IGNORECASE,
            )
            if match:
                expected_sql[match.group(1)] = statement
        actual_sql = {
            name: sql
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        }
        actual_objects = {
            name
            for (name,) in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type IN ('table', 'index', 'trigger', 'view')
                     AND name NOT LIKE 'sqlite_%'"""
            )
        }
        normalize_sql = lambda value: re.sub(r"\s+", " ", value).strip().rstrip(";")
        if actual_objects != set(expected_sql) or any(
            name not in actual_sql
            or normalize_sql(actual_sql[name]) != normalize_sql(statement)
            for name, statement in expected_sql.items()
        ):
            raise RuntimeError("Malformed schema-v1 SQL metadata")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")

    @classmethod
    def _validate_completion_rows(cls, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """SELECT application_id, attempt_id, attempt_number, job_url,
                      normalized_job_url, payload_fingerprint, browser_action_id,
                      evidence_id, evidence_json, application_status,
                      automation_status, applied_date, completed_at
               FROM submission_completions"""
        ).fetchall()
        for row in rows:
            cls._from_row(row)

    @classmethod
    def _validate_schema(cls, connection: sqlite3.Connection, version: int) -> None:
        if version != cls.SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported submission-state schema version: {version}")
        cls._validate_completion_schema(connection)
        for table, expected_columns in cls._V2_COLUMNS.items():
            if cls._table_columns(connection, table) != expected_columns:
                raise RuntimeError(f"Missing or malformed schema-v2 table: {table}")
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        if not cls._V2_TRIGGER_NAMES.issubset(triggers):
            raise RuntimeError("Missing or malformed schema-v2 trigger metadata")
        expected_sql: dict[str, str] = {}
        for statement in (*cls._v1_statements(), *cls._v2_statements()):
            match = re.match(
                r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER)\s+([A-Za-z_][A-Za-z0-9_]*)",
                statement.strip(),
                flags=re.IGNORECASE,
            )
            if match:
                expected_sql[match.group(1)] = statement
        actual_sql = {
            name: sql
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        }
        actual_objects = {
            name
            for (name,) in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type IN ('table', 'index', 'trigger', 'view')
                     AND name NOT LIKE 'sqlite_%'"""
            )
        }
        normalize_sql = lambda value: re.sub(r"\s+", " ", value).strip().rstrip(";")
        if actual_objects != set(expected_sql) or any(
            name not in actual_sql
            or normalize_sql(actual_sql[name]) != normalize_sql(statement)
            for name, statement in expected_sql.items()
        ):
            raise RuntimeError("Malformed schema-v2 SQL metadata")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        fk_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError("SQLite foreign-key integrity check failed")

    @staticmethod
    def _select(connection: sqlite3.Connection, key: str, value: str):
        if key not in {"application_id", "normalized_job_url"}:
            raise ValueError("Unsupported completion lookup key")
        return connection.execute(
            f"""SELECT application_id, attempt_id, attempt_number, job_url,
                       normalized_job_url, payload_fingerprint, browser_action_id,
                       evidence_id, evidence_json, application_status,
                       automation_status, applied_date, completed_at
                FROM submission_completions WHERE {key} = ?""",
            (value,),
        ).fetchone()

    @staticmethod
    def _from_row(row) -> SubmissionCompletion | None:
        if row is None:
            return None
        names = (
            "application_id", "attempt_id", "attempt_number", "job_url",
            "normalized_job_url", "payload_fingerprint", "browser_action_id",
            "evidence_id", "evidence_json", "application_status",
            "automation_status", "applied_date", "completed_at",
        )
        data = dict(zip(names, row, strict=True))
        evidence = VerifiedSubmissionEvidence.model_validate(
            json.loads(data.pop("evidence_json"))
        )
        completion = SubmissionCompletion(**data, evidence=evidence)
        if (
            completion.normalized_job_url != normalize_job_url(completion.job_url)
            or completion.attempt_id
            != f"{completion.application_id}:{completion.attempt_number}"
            or completion.evidence_id != SQLiteSubmissionState.evidence_id(evidence)
            or completion.browser_action_id != evidence.observation.browser_action_id
            or not evidence.validates_for(
                application_id=completion.application_id,
                job_url=completion.normalized_job_url,
                attempt_number=completion.attempt_number,
                payload_fingerprint=completion.payload_fingerprint,
            )
        ):
            raise ValueError("Corrupt or mismatched SQLite completion record")
        return completion

    def get_for_application(self, application_id: str) -> SubmissionCompletion | None:
        connection = self._connect()
        try:
            return self._from_row(self._select(connection, "application_id", application_id))
        finally:
            connection.close()

    def get_for_url(self, normalized_job_url: str) -> SubmissionCompletion | None:
        connection = self._connect()
        try:
            return self._from_row(
                self._select(connection, "normalized_job_url", normalized_job_url)
            )
        finally:
            connection.close()

    def list_completions(self) -> list[SubmissionCompletion]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT application_id, attempt_id, attempt_number, job_url,
                          normalized_job_url, payload_fingerprint, browser_action_id,
                          evidence_id, evidence_json, application_status,
                          automation_status, applied_date, completed_at
                   FROM submission_completions ORDER BY completed_at"""
            ).fetchall()
            result = [self._from_row(row) for row in rows]
            if any(item is None for item in result):
                raise RuntimeError("Invalid empty submission completion record")
            return result
        finally:
            connection.close()

    def commit_completion(
        self, *, application_id: str, attempt_number: int, job_url: str,
        payload_fingerprint: str, browser_action_id: str,
        evidence: VerifiedSubmissionEvidence, applied_date: date,
    ) -> tuple[SubmissionCompletion, bool]:
        normalized_url = normalize_job_url(job_url)
        if not evidence.validates_for(
            application_id=application_id, job_url=normalized_url,
            attempt_number=attempt_number, payload_fingerprint=payload_fingerprint,
        ):
            raise ValueError("Verified evidence does not match the completion context")
        if not browser_action_id or browser_action_id != evidence.observation.browser_action_id:
            raise ValueError("Completion evidence is not bound to its browser action")
        evidence_json = json.dumps(
            evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        completion = SubmissionCompletion(
            application_id=application_id,
            attempt_id=f"{application_id}:{attempt_number}",
            attempt_number=attempt_number,
            job_url=job_url,
            normalized_job_url=normalized_url,
            payload_fingerprint=payload_fingerprint,
            browser_action_id=browser_action_id,
            evidence_id=self.evidence_id(evidence),
            evidence=evidence,
            applied_date=applied_date,
            completed_at=datetime.now(timezone.utc),
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._from_row(self._select(connection, "application_id", application_id))
            if existing is not None:
                if (
                    existing.attempt_id != completion.attempt_id
                    or existing.normalized_job_url != normalized_url
                    or existing.payload_fingerprint != payload_fingerprint
                    or existing.browser_action_id != browser_action_id
                    or existing.evidence_id != completion.evidence_id
                    or existing.evidence != evidence
                ):
                    raise ValueError("Conflicting completion already exists")
                connection.rollback()
                return existing, False
            connection.execute(
                """INSERT INTO submission_completions (
                       application_id, attempt_id, attempt_number, job_url,
                       normalized_job_url, payload_fingerprint, browser_action_id,
                       evidence_id, evidence_json, attempt_outcome,
                       submission_outcome, application_status, automation_status,
                       applied_date, completed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', 'submitted',
                             'applied', 'submitted', ?, ?)""",
                (
                    completion.application_id, completion.attempt_id,
                    completion.attempt_number, completion.job_url,
                    completion.normalized_job_url, completion.payload_fingerprint,
                    completion.browser_action_id, completion.evidence_id,
                    evidence_json, applied_date.isoformat(),
                    completion.completed_at.isoformat(),
                ),
            )
            self._before_commit(connection)
            connection.commit()
            return completion, True
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _before_commit(self, connection: sqlite3.Connection) -> None:
        return None
