"""Read-only, deterministic planning for legacy application lifecycle JSON.

This module deliberately has no migration/write operation. JSON evidence and
authorization fields are reported as untrusted artifacts and omitted from
proposed history records.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field, ValidationError

from src.applications.evidence import BrowserObservation, VerifiedSubmissionEvidence
from src.applications.identity import application_id_for
from src.applications.models import Application, ApplicationStatus, AutomationStatus
from src.applications.persistence import SQLiteSubmissionState
from src.applications.recovery import (
    ApplicationAttempt,
    AttemptOutcome,
    SubmissionOutcome,
)
from src.research.job_normalizer import normalize_job_url


class PlanDisposition(StrEnum):
    importable = "importable"
    quarantined = "quarantined"
    excluded = "excluded"


class SourceState(StrEnum):
    present = "present"
    missing = "missing"
    not_provided = "not_provided"
    corrupt = "corrupt"


class MigrationPlanRecord(BaseModel):
    record_id: str
    source_category: str
    disposition: PlanDisposition
    reason_codes: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    proposed_import: dict[str, Any] | None = None


class MigrationPlanSource(BaseModel):
    category: str
    state: SourceState
    sha256: str | None = None
    records_read: int = 0
    validation_errors: list[str] = Field(default_factory=list)


class MigrationPlan(BaseModel):
    planner_version: str = "1"
    target_schema_version: int = 2
    sources: list[MigrationPlanSource]
    records: list[MigrationPlanRecord]
    importable_count: int
    quarantined_count: int
    excluded_count: int
    proposed_import_summary: list[dict[str, Any]]

    def canonical_json(self) -> str:
        """Return stable JSON suitable for dry-run comparison or review."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


class _JSONInput:
    def __init__(self, category: str, path: str | Path | None) -> None:
        self.category = category
        self.path = Path(path) if path is not None else None
        self.state = SourceState.not_provided if path is None else SourceState.missing
        self.digest: str | None = None
        self.rows: list[Any] = []
        self.error: str | None = None

        if self.path is None or not self.path.exists():
            return

        content = self.path.read_bytes()
        self.digest = hashlib.sha256(content).hexdigest()
        try:
            value = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.state = SourceState.corrupt
            self.error = f"{type(exc).__name__}: {exc}"
            return

        if not isinstance(value, list):
            self.state = SourceState.corrupt
            self.error = "Top-level JSON value must be an array."
            return
        self.state = SourceState.present
        self.rows = value


class _ApplicationCandidate:
    def __init__(self, index: int, raw: Any, application: Application) -> None:
        self.index = index
        self.raw = raw
        self.application = application
        self.application_id = application_id_for(application.job_url)
        self.normalized_url = normalize_job_url(application.job_url)
        self.record_id = f"application:{self.application_id}#{index + 1}"
        self.conflicts: set[str] = set()
        self.reason_codes: set[str] = set()
        self.quarantined = False
        self.completion_match = False


class _AttemptCandidate:
    def __init__(
        self,
        index: int,
        raw: Any,
        attempt: ApplicationAttempt,
        observation: BrowserObservation | None,
        evidence: VerifiedSubmissionEvidence | None,
        observation_error: str | None,
        evidence_error: str | None,
    ) -> None:
        self.index = index
        self.raw = raw
        self.attempt = attempt
        self.observation = observation
        self.evidence = evidence
        self.observation_error = observation_error
        self.evidence_error = evidence_error
        self.record_id = (
            f"attempt:{attempt.application_id}:{attempt.attempt_number}#{index + 1}"
        )
        self.conflicts: set[str] = set()
        self.reason_codes: set[str] = set()
        self.quarantined = False
        self.completion_match = False


class _CompletionCandidate:
    def __init__(self, row: dict[str, Any], evidence: VerifiedSubmissionEvidence) -> None:
        self.row = row
        self.evidence = evidence
        self.record_id = f"sqlite_completion:{row.get('application_id', 'unknown')}"
        self.errors: list[str] = []


def _validation_errors(exc: ValidationError) -> list[str]:
    errors = []
    for item in exc.errors(include_input=False):
        location = ".".join(str(part) for part in item.get("loc", ())) or "record"
        errors.append(f"{location}: {item.get('msg', 'invalid value')}")
    return sorted(errors)


def _parse_evidence(raw: Any) -> tuple[VerifiedSubmissionEvidence | None, str | None]:
    if raw is None:
        return None, None
    try:
        return VerifiedSubmissionEvidence.model_validate(raw), None
    except ValidationError as exc:
        return None, "; ".join(_validation_errors(exc))


def _parse_observation(raw: Any) -> tuple[BrowserObservation | None, str | None]:
    if raw is None:
        return None, None
    try:
        return BrowserObservation.model_validate(raw), None
    except ValidationError as exc:
        return None, "; ".join(_validation_errors(exc))


def _completion_inputs(
    path: str | Path | None,
) -> tuple[MigrationPlanSource, list[_CompletionCandidate], list[MigrationPlanRecord]]:
    if path is None:
        return (
            MigrationPlanSource(category="sqlite_completions", state=SourceState.not_provided),
            [],
            [],
        )

    database = Path(path)
    if not database.exists():
        return (
            MigrationPlanSource(category="sqlite_completions", state=SourceState.missing),
            [],
            [],
        )

    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    uri = f"{database.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("submission_completions",),
            ).fetchone()
            if version == 0 and table is None:
                rows: list[sqlite3.Row] = []
            elif version not in {1, SQLiteSubmissionState.SCHEMA_VERSION} or table is None:
                raise ValueError("Unsupported or incomplete SQLite submission schema.")
            else:
                if version == SQLiteSubmissionState.SCHEMA_VERSION:
                    SQLiteSubmissionState._validate_schema(connection, version)
                else:
                    SQLiteSubmissionState._validate_v1_schema(connection)
                rows = connection.execute(
                    """SELECT application_id, attempt_id, attempt_number, job_url,
                              normalized_job_url, payload_fingerprint,
                              browser_action_id, evidence_id, evidence_json,
                              application_status, automation_status
                       FROM submission_completions ORDER BY application_id"""
                ).fetchall()
        finally:
            connection.close()
    except (sqlite3.DatabaseError, OSError, ValueError, RuntimeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        source = MigrationPlanSource(
            category="sqlite_completions",
            state=SourceState.corrupt,
            sha256=digest,
            validation_errors=[error],
        )
        record = MigrationPlanRecord(
            record_id="sqlite_completions:database",
            source_category="sqlite_completion",
            disposition=PlanDisposition.quarantined,
            reason_codes=["sqlite_source_unreadable"],
            validation_errors=[error],
        )
        return source, [], [record]

    candidates: list[_CompletionCandidate] = []
    records: list[MigrationPlanRecord] = []
    for row in rows:
        values = dict(row)
        record_id = f"sqlite_completion:{values.get('application_id', 'unknown')}"
        errors: list[str] = []
        try:
            evidence = VerifiedSubmissionEvidence.model_validate(
                json.loads(values["evidence_json"])
            )
        except (KeyError, json.JSONDecodeError, TypeError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                errors.extend(_validation_errors(exc))
            else:
                errors.append(f"{type(exc).__name__}: {exc}")
            evidence = None

        if evidence is not None:
            normalized = normalize_job_url(str(values["job_url"]))
            if normalized != values["normalized_job_url"]:
                errors.append("normalized_job_url does not match job_url")
            if values["attempt_id"] != f"{values['application_id']}:{values['attempt_number']}":
                errors.append("attempt_id does not match application and attempt number")
            if values["application_id"] != application_id_for(values["job_url"]):
                errors.append("application_id does not match normalized job URL")
            if values["evidence_id"] != SQLiteSubmissionState.evidence_id(evidence):
                errors.append("evidence_id does not match canonical evidence")
            if values["browser_action_id"] != evidence.observation.browser_action_id:
                errors.append("browser_action_id does not match evidence observation")
            if values["application_status"] != ApplicationStatus.applied.value:
                errors.append("completion application_status is not applied")
            if values["automation_status"] != AutomationStatus.submitted.value:
                errors.append("completion automation_status is not submitted")
            if not evidence.validates_for(
                application_id=values["application_id"],
                job_url=normalized,
                attempt_number=values["attempt_number"],
                payload_fingerprint=values["payload_fingerprint"],
            ):
                errors.append("evidence does not validate for completion context")
            if not errors:
                candidates.append(_CompletionCandidate(values, evidence))

        if errors:
            records.append(
                MigrationPlanRecord(
                    record_id=record_id,
                    source_category="sqlite_completion",
                    disposition=PlanDisposition.quarantined,
                    reason_codes=["sqlite_completion_invalid"],
                    validation_errors=sorted(set(errors)),
                )
            )
        else:
            records.append(
                MigrationPlanRecord(
                    record_id=record_id,
                    source_category="sqlite_completion",
                    disposition=PlanDisposition.excluded,
                    reason_codes=["completion_already_authoritative_not_imported"],
                )
            )

    source = MigrationPlanSource(
        category="sqlite_completions",
        state=SourceState.present,
        sha256=digest,
        records_read=len(rows),
    )
    return source, candidates, records


def plan_legacy_lifecycle_migration(
    *,
    applications_path: str | Path | None,
    attempts_path: str | Path | None,
    sqlite_path: str | Path | None,
) -> MigrationPlan:
    """Inspect legacy snapshots and return a stable plan without writing files.

    ``None`` means not supplied; a supplied but absent path is reported as
    missing. Application/attempt JSON files are read once as byte snapshots.
    The SQLite database is opened with ``mode=ro`` and ``query_only``.
    """

    app_input = _JSONInput("applications", applications_path)
    attempt_input = _JSONInput("attempts", attempts_path)
    sqlite_source, completion_candidates, records = _completion_inputs(sqlite_path)
    sources = [app_input, attempt_input]

    applications: list[_ApplicationCandidate] = []
    attempts: list[_AttemptCandidate] = []

    for source in sources:
        if source.state == SourceState.corrupt:
            records.append(
                MigrationPlanRecord(
                    record_id=f"{source.category}:source",
                    source_category=source.category,
                    disposition=PlanDisposition.quarantined,
                    reason_codes=["json_source_corrupt"],
                    validation_errors=[source.error or "Invalid JSON source."],
                )
            )
            continue
        if source.state != SourceState.present:
            continue
        for index, raw in enumerate(source.rows):
            if source.category == "applications":
                try:
                    application = Application.model_validate(raw)
                except ValidationError as exc:
                    record_id = f"application:row#{index + 1}"
                    records.append(
                        MigrationPlanRecord(
                            record_id=record_id,
                            source_category="application",
                            disposition=PlanDisposition.quarantined,
                            reason_codes=["application_record_malformed"],
                            validation_errors=_validation_errors(exc),
                        )
                    )
                    continue
                applications.append(_ApplicationCandidate(index, raw, application))
            else:
                if not isinstance(raw, dict):
                    records.append(
                        MigrationPlanRecord(
                            record_id=f"attempt:row#{index + 1}",
                            source_category="attempt",
                            disposition=PlanDisposition.quarantined,
                            reason_codes=["attempt_record_malformed"],
                            validation_errors=["Attempt record must be a JSON object."],
                        )
                    )
                    continue
                base = dict(raw)
                observation_raw = base.pop("browser_observation", None)
                evidence_raw = base.pop("verified_evidence_record", None)
                # Authorization and browser-action data are not needed to
                # validate/import the non-authorizing historical attempt.
                base["browser_action_started"] = False
                base["browser_action_id"] = None
                base["verified_evidence_consumed"] = False
                try:
                    attempt = ApplicationAttempt.model_validate(base)
                except ValidationError as exc:
                    records.append(
                        MigrationPlanRecord(
                            record_id=f"attempt:row#{index + 1}",
                            source_category="attempt",
                            disposition=PlanDisposition.quarantined,
                            reason_codes=["attempt_record_malformed"],
                            validation_errors=_validation_errors(exc),
                        )
                    )
                    continue
                observation, observation_error = _parse_observation(observation_raw)
                evidence, evidence_error = _parse_evidence(evidence_raw)
                if observation is not None and not observation.matches(
                    application_id=attempt.application_id,
                    job_url=attempt.job_url,
                    attempt_number=attempt.attempt_number,
                    payload_fingerprint=attempt.payload_fingerprint or "",
                ):
                    observation_error = "Observation does not match attempt context."
                if (
                    observation is not None
                    and raw.get("browser_action_id") != observation.browser_action_id
                ):
                    observation_error = "Observation action ID does not match attempt action ID."
                if evidence is not None and not evidence.validates_for(
                    application_id=attempt.application_id,
                    job_url=attempt.job_url,
                    attempt_number=attempt.attempt_number,
                    payload_fingerprint=attempt.payload_fingerprint or "",
                ):
                    evidence_error = "Evidence does not match attempt context."
                if evidence is not None and evidence.observation != observation:
                    evidence_error = "Evidence observation does not match persisted observation."
                attempts.append(
                    _AttemptCandidate(
                        index,
                        raw,
                        attempt,
                        observation,
                        evidence,
                        observation_error,
                        evidence_error,
                    )
                )

    # Duplicate application URLs and attempt identities quarantine all parties.
    by_url: dict[str, list[_ApplicationCandidate]] = {}
    for candidate in applications:
        by_url.setdefault(candidate.normalized_url, []).append(candidate)
    for same_url in by_url.values():
        if len(same_url) > 1:
            ids = sorted(item.record_id for item in same_url)
            for item in same_url:
                item.quarantined = True
                item.reason_codes.add("duplicate_normalized_job_url")
                item.conflicts.update(set(ids) - {item.record_id})

    attempt_keys: dict[tuple[str, int], list[_AttemptCandidate]] = {}
    for candidate in attempts:
        key = (candidate.attempt.application_id, candidate.attempt.attempt_number)
        attempt_keys.setdefault(key, []).append(candidate)
    for same_key in attempt_keys.values():
        if len(same_key) > 1:
            ids = sorted(item.record_id for item in same_key)
            for item in same_key:
                item.quarantined = True
                item.reason_codes.add("duplicate_attempt_identity")
                item.conflicts.update(set(ids) - {item.record_id})

    apps_by_id: dict[str, list[_ApplicationCandidate]] = {}
    for candidate in applications:
        apps_by_id.setdefault(candidate.application_id, []).append(candidate)

    attempts_by_id: dict[str, list[_AttemptCandidate]] = {}
    for candidate in attempts:
        attempts_by_id.setdefault(candidate.attempt.application_id, []).append(candidate)
        if candidate.attempt.application_id != application_id_for(candidate.attempt.job_url):
            candidate.quarantined = True
            candidate.reason_codes.add("attempt_application_id_url_mismatch")
        linked = apps_by_id.get(candidate.attempt.application_id, [])
        if not linked:
            candidate.quarantined = True
            candidate.reason_codes.add("attempt_application_missing")
        elif all(item.normalized_url != normalize_job_url(candidate.attempt.job_url) for item in linked):
            candidate.quarantined = True
            candidate.reason_codes.add("attempt_job_url_conflicts_with_application")
            candidate.conflicts.update(item.record_id for item in linked)
            for item in linked:
                item.quarantined = True
                item.reason_codes.add("attempt_job_url_conflict")
                item.conflicts.add(candidate.record_id)

    completions_by_id: dict[str, list[_CompletionCandidate]] = {}
    for completion in completion_candidates:
        completions_by_id.setdefault(completion.row["application_id"], []).append(completion)
    for same_id in completions_by_id.values():
        if len(same_id) > 1:
            ids = sorted(item.record_id for item in same_id)
            duplicate_ids = set(ids)
            records = [
                record
                for record in records
                if not (
                    record.source_category == "sqlite_completion"
                    and record.record_id in duplicate_ids
                )
            ]
            for item in same_id:
                item.errors.append("Duplicate SQLite completion application identity.")
                records.append(
                    MigrationPlanRecord(
                        record_id=item.record_id,
                        source_category="sqlite_completion",
                        disposition=PlanDisposition.quarantined,
                        reason_codes=["duplicate_sqlite_completion_identity"],
                        validation_errors=["Duplicate SQLite completion application identity."],
                        conflicts_with=sorted(set(ids) - {item.record_id}),
                    )
                )

    def matching_completion(app: _ApplicationCandidate) -> _CompletionCandidate | None:
        candidates = completions_by_id.get(app.application_id, [])
        if len(candidates) != 1:
            return None
        completion = candidates[0]
        row = completion.row
        evidence = completion.evidence
        if (
            normalize_job_url(row["job_url"]) != app.normalized_url
            or (
                app.application.submission_evidence is not None
                and app.application.submission_evidence != evidence.evidence
            )
            or app.application.automation_status
            not in {AutomationStatus.submitting, AutomationStatus.submitted}
        ):
            return None
        if app.application.automation_status == AutomationStatus.submitting:
            if app.application.status not in {
                ApplicationStatus.discovered,
                ApplicationStatus.ready,
            }:
                return None
        elif app.application.status not in {
            ApplicationStatus.applied,
            ApplicationStatus.interview,
            ApplicationStatus.offer,
            ApplicationStatus.rejected,
            ApplicationStatus.withdrawn,
        }:
            return None
        attempt_matches = [
            attempt
            for attempt in attempts_by_id.get(app.application_id, [])
            if attempt.attempt.attempt_number == row["attempt_number"]
        ]
        if len(attempt_matches) != 1:
            return None
        attempt = attempt_matches[0]
        parsed_full_attempt = None
        try:
            parsed_full_attempt = ApplicationAttempt.model_validate(attempt.raw)
        except ValidationError:
            return None
        if (
            attempt.quarantined
            or normalize_job_url(attempt.attempt.job_url) != app.normalized_url
            or attempt.attempt.payload_fingerprint != row["payload_fingerprint"]
            or attempt.attempt.outcome
            not in {AttemptOutcome.submitting, AttemptOutcome.submitted}
            or attempt.attempt.submission_outcome != SubmissionOutcome.submitted
            or attempt.evidence != evidence
            or parsed_full_attempt.browser_action_started is not True
            or parsed_full_attempt.browser_action_id != evidence.observation.browser_action_id
            or parsed_full_attempt.browser_observation != evidence.observation
            or parsed_full_attempt.verified_evidence_record != evidence
            or parsed_full_attempt.verification_result not in {None, True}
            or parsed_full_attempt.verification_evidence not in {None, evidence.evidence}
        ):
            return None
        app.completion_match = True
        attempt.completion_match = True
        return completion

    matching_completions: dict[str, _CompletionCandidate | None] = {}
    completion_conflicts: dict[str, set[str]] = {}
    for app in applications:
        completion = matching_completion(app)
        matching_completions[app.record_id] = completion
        candidates = completions_by_id.get(app.application_id, [])
        if candidates and completion is None:
            app.quarantined = True
            app.reason_codes.add("sqlite_completion_conflicts_with_legacy_context")
            app.conflicts.update(item.record_id for item in candidates)
            for item in candidates:
                completion_conflicts.setdefault(item.record_id, set()).add(app.record_id)
            for attempt in attempts_by_id.get(app.application_id, []):
                if any(
                    attempt.attempt.attempt_number == item.row["attempt_number"]
                    for item in candidates
                ):
                    attempt.quarantined = True
                    attempt.reason_codes.add("sqlite_completion_attempt_context_conflict")
                    attempt.conflicts.update(item.record_id for item in candidates)
                    for item in candidates:
                        completion_conflicts.setdefault(item.record_id, set()).add(
                            attempt.record_id
                        )

    for app in applications:
        completion = matching_completions[app.record_id]
        success_claim = (
            app.application.status == ApplicationStatus.applied
            or app.application.automation_status == AutomationStatus.submitted
        )
        if success_claim and completion is None:
            app.quarantined = True
            app.reason_codes.add("legacy_success_without_matching_sqlite_completion")
        if app.quarantined:
            records.append(
                MigrationPlanRecord(
                    record_id=app.record_id,
                    source_category="application",
                    disposition=PlanDisposition.quarantined,
                    reason_codes=sorted(app.reason_codes or {"application_conflict"}),
                    conflicts_with=sorted(app.conflicts),
                )
            )
            continue
        application = app.application
        automation_status = application.automation_status
        business_status = application.status
        if automation_status == AutomationStatus.submitting:
            automation_status = AutomationStatus.unknown_submission_result
        if completion is not None and business_status in {
            ApplicationStatus.discovered,
            ApplicationStatus.ready,
        }:
            business_status = ApplicationStatus.applied
            automation_status = AutomationStatus.submitted
        records.append(
            MigrationPlanRecord(
                record_id=app.record_id,
                source_category="application",
                disposition=PlanDisposition.importable,
                reason_codes=(
                    ["state_backed_by_existing_sqlite_completion"]
                    if completion is not None
                    else ["non_authorizing_application_history"]
                ),
                proposed_import={
                    "application_id": app.application_id,
                    "business_status": business_status.value,
                    "automation_status": automation_status.value,
                },
            )
        )

    for attempt in attempts:
        item = attempt.attempt
        if attempt.observation_error:
            records.append(
                MigrationPlanRecord(
                    record_id=f"{attempt.record_id}:browser_observation",
                    source_category="browser_observation",
                    disposition=PlanDisposition.quarantined,
                    reason_codes=[
                        "legacy_browser_observation_malformed"
                        if attempt.observation is None
                        else "legacy_browser_observation_context_mismatch"
                    ],
                    validation_errors=[attempt.observation_error],
                )
            )
        elif attempt.raw.get("browser_observation") is not None:
            records.append(
                MigrationPlanRecord(
                    record_id=f"{attempt.record_id}:browser_observation",
                    source_category="browser_observation",
                    disposition=PlanDisposition.excluded,
                    reason_codes=["legacy_browser_observation_untrusted"],
                )
            )
        if attempt.evidence_error:
            records.append(
                MigrationPlanRecord(
                    record_id=f"{attempt.record_id}:verified_evidence",
                    source_category="verified_evidence",
                    disposition=PlanDisposition.quarantined,
                    reason_codes=[
                        "legacy_evidence_malformed"
                        if attempt.evidence is None
                        else "legacy_evidence_context_mismatch"
                    ],
                    validation_errors=[attempt.evidence_error],
                )
            )
        elif attempt.raw.get("verified_evidence_record") is not None:
            records.append(
                MigrationPlanRecord(
                    record_id=f"{attempt.record_id}:verified_evidence",
                    source_category="verified_evidence",
                    disposition=PlanDisposition.excluded,
                    reason_codes=["legacy_evidence_never_trusted_by_import"],
                )
            )
        if (
            attempt.raw.get("browser_action_started")
            or attempt.raw.get("browser_action_id")
            or attempt.raw.get("verified_evidence_consumed")
        ):
            records.append(
                MigrationPlanRecord(
                    record_id=f"{attempt.record_id}:authorization",
                    source_category="submission_authorization",
                    disposition=PlanDisposition.excluded,
                    reason_codes=["legacy_claim_or_action_never_authorizes"],
                )
            )

        if attempt.observation_error or attempt.evidence_error:
            attempt.quarantined = True
            if attempt.observation_error:
                attempt.reason_codes.add("attempt_observation_context_conflict")
            if attempt.evidence_error:
                attempt.reason_codes.add("attempt_evidence_context_conflict")

        if attempt.quarantined:
            records.append(
                MigrationPlanRecord(
                    record_id=attempt.record_id,
                    source_category="attempt",
                    disposition=PlanDisposition.quarantined,
                    reason_codes=sorted(attempt.reason_codes or {"attempt_conflict"}),
                    conflicts_with=sorted(attempt.conflicts),
                )
            )
            continue
        if item.outcome == AttemptOutcome.submitted and not attempt.completion_match:
            records.append(
                MigrationPlanRecord(
                    record_id=attempt.record_id,
                    source_category="attempt",
                    disposition=PlanDisposition.quarantined,
                    reason_codes=["legacy_submitted_attempt_without_matching_completion"],
                )
            )
            continue
        mapped_outcome = item.outcome
        mapped_submission_outcome = item.submission_outcome
        reason_codes = ["non_authorizing_attempt_history"]
        if item.outcome == AttemptOutcome.submitting:
            mapped_outcome = AttemptOutcome.unknown_submission_result
            mapped_submission_outcome = SubmissionOutcome.unknown
            reason_codes = ["stale_submitting_mapped_to_blocked_unknown"]
        elif item.outcome == AttemptOutcome.unknown_submission_result:
            mapped_submission_outcome = SubmissionOutcome.unknown
            reason_codes = ["unknown_remains_blocked"]
        elif item.outcome == AttemptOutcome.submitted:
            reason_codes = ["submitted_history_backed_by_existing_completion"]
        records.append(
            MigrationPlanRecord(
                record_id=attempt.record_id,
                source_category="attempt",
                disposition=PlanDisposition.importable,
                reason_codes=reason_codes,
                proposed_import={
                    "application_id": item.application_id,
                    "attempt_number": item.attempt_number,
                    "outcome": mapped_outcome.value,
                    "submission_outcome": mapped_submission_outcome.value,
                    "retryable": False,
                },
            )
        )

    for index, record in enumerate(records):
        conflicting_ids = completion_conflicts.get(record.record_id)
        if (
            record.source_category == "sqlite_completion"
            and conflicting_ids
            and record.disposition != PlanDisposition.quarantined
        ):
            records[index] = record.model_copy(
                update={
                    "disposition": PlanDisposition.quarantined,
                    "reason_codes": sorted(
                        {*record.reason_codes, "sqlite_completion_legacy_context_conflict"}
                    ),
                    "conflicts_with": sorted(conflicting_ids),
                }
            )

    counts = {
        disposition: sum(record.disposition == disposition for record in records)
        for disposition in PlanDisposition
    }
    records.sort(key=lambda record: (record.source_category, record.record_id, record.disposition.value))
    sources = [
        MigrationPlanSource(
            category=item.category,
            state=item.state,
            sha256=item.digest,
            records_read=len(item.rows) if item.state == SourceState.present else 0,
            validation_errors=[item.error] if item.error else [],
        )
        for item in sources
    ]
    sources.append(sqlite_source)
    sources.sort(key=lambda item: item.category)
    summary = [
        {"record_id": record.record_id, "source_category": record.source_category, **record.proposed_import}
        for record in records
        if record.disposition == PlanDisposition.importable and record.proposed_import is not None
    ]
    return MigrationPlan(
        sources=sources,
        records=records,
        importable_count=counts[PlanDisposition.importable],
        quarantined_count=counts[PlanDisposition.quarantined],
        excluded_count=counts[PlanDisposition.excluded],
        proposed_import_summary=summary,
    )
