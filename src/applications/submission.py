from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from src.applications.models import Application, AutomationStatus
from src.applications.profile import ApplicationProfile
from src.applications.recovery import (
    ApplicationAttempt,
    AttemptOutcome,
    AttemptStore,
    FailureCategory,
    ReviewQuestionSnapshot,
    SubmissionOutcome,
)
from src.applications.tracker import ApplicationTracker
from src.research.job_normalizer import normalize_job_url
from src.research.models import Job


class QuestionType(StrEnum):
    text = "text"
    file = "file"


class VerificationOutcome(StrEnum):
    verified = "verified"
    not_submitted = "not_submitted"
    unknown = "unknown"


class ApplicationQuestion(BaseModel):
    key: str
    label: str
    required: bool = True
    question_type: QuestionType = QuestionType.text


class ApplicationForm(BaseModel):
    platform: str
    application_url: str
    questions: list[ApplicationQuestion] = Field(default_factory=list)
    human_action_required: bool = False
    human_action_reason: str | None = None


class ApplicationCapabilities(BaseModel):
    prepare: bool = True
    fill_known_fields: bool = False
    resume_upload: bool = False
    submit: bool = False
    verify_submission: bool = False


class ApplicationPayload(BaseModel):
    application_id: str
    company: str
    job_title: str
    job_url: str
    application_url: str
    platform: str
    fields: dict[str, str] = Field(default_factory=dict)
    review_questions: list[ApplicationQuestion] = Field(default_factory=list)
    unanswered_review_questions: list[ApplicationQuestion] = Field(default_factory=list)
    missing_profile_questions: list[ApplicationQuestion] = Field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(
            self.unanswered_review_questions or self.missing_profile_questions
        )


class SubmissionReceipt(BaseModel):
    submission_outcome: SubmissionOutcome
    failure_category: FailureCategory | None = None
    error_message: str | None = None
    retryable: bool = False
    human_action_required: bool = False


class FillResult(BaseModel):
    success: bool = True
    human_action_required: bool = False
    failure_category: FailureCategory | None = None
    error_message: str | None = None
    retryable: bool = False


class SubmissionVerification(BaseModel):
    outcome: VerificationOutcome
    evidence: str | None = None


class AdapterError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        submission_started: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.submission_started = submission_started


class ApplicationAdapter(Protocol):
    platform: str

    def can_handle(self, job: Job) -> bool: ...

    def capabilities(self, job: Job) -> ApplicationCapabilities: ...

    def prepare(self, job: Job, profile: ApplicationProfile) -> ApplicationForm: ...

    def fill(self, payload: ApplicationPayload) -> FillResult: ...

    def submit(self, payload: ApplicationPayload) -> SubmissionReceipt: ...

    def verify_result(
        self,
        payload: ApplicationPayload,
        receipt: SubmissionReceipt,
    ) -> SubmissionVerification: ...


SAFE_FIELD_ALIASES = {
    "name": {"name", "full name", "legal name"},
    "email": {"email", "email address"},
    "phone": {"phone", "phone number", "telephone"},
    "address": {"address", "street address", "address line 1"},
    "city": {"city"},
    "state": {"state", "province", "region"},
    "country": {"country"},
    "linkedin": {"linkedin", "linkedin url", "linkedin profile"},
    "github": {"github", "github url", "github profile"},
    "portfolio": {"portfolio", "portfolio url"},
    "degree": {"degree"},
    "institution": {"institution", "school", "university"},
    "graduation_date": {"graduation date", "graduation year"},
    "skills": {"skills", "technical skills"},
    "tools": {"tools"},
    "technologies": {"technologies", "technology"},
    "experience": {"experience", "work experience", "employment history"},
    "projects": {"projects", "project experience"},
    "certifications": {"certifications", "certificates"},
    "resume": {"resume", "cv", "resume upload", "attach a cv"},
}
CONFIGURED_FIELD_ALIASES = {
    "work_authorization": {"work authorization", "authorized to work"},
    "requires_sponsorship": {
        "require sponsorship",
        "sponsorship required",
        "need sponsorship",
    },
    "relocation_preference": {"relocation", "willing to relocate"},
    "notice_period": {"notice period", "availability"},
    "salary_expectation": {"salary expectation", "salary requirements"},
    "employment_type": {"employment type"},
}


def _normalize_question(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def application_id_for(job_url: str) -> str:
    return sha256(normalize_job_url(job_url).encode("utf-8")).hexdigest()


def _experience_text(profile: ApplicationProfile) -> str | None:
    if not profile.experience:
        return None

    entries = []
    for item in profile.experience:
        dates = " - ".join(part for part in (item.start_date, item.end_date) if part)
        heading = " | ".join(part for part in (item.role, item.company, dates) if part)
        responsibilities = "; ".join(item.responsibilities)
        entries.append("\n".join(part for part in (heading, responsibilities) if part))

    return "\n\n".join(entries)


def _profile_values(profile: ApplicationProfile) -> dict[str, str | Path | None]:
    education = profile.education[0] if len(profile.education) == 1 else None
    return {
        "name": profile.name,
        "email": profile.email,
        "phone": profile.phone,
        "address": profile.address,
        "city": profile.city,
        "state": profile.state,
        "country": profile.country,
        "linkedin": profile.links.linkedin,
        "github": profile.links.github,
        "portfolio": profile.links.portfolio,
        "degree": education.degree if education else None,
        "institution": education.institution if education else None,
        "graduation_date": education.graduation_date if education else None,
        "skills": ", ".join(profile.skills) if profile.skills else None,
        "tools": ", ".join(profile.tools) if profile.tools else None,
        "technologies": ", ".join(profile.technologies)
        if profile.technologies
        else None,
        "experience": _experience_text(profile),
        "projects": "\n\n".join(
            "\n".join(
                part
                for part in (
                    project.name,
                    project.description,
                    ", ".join(project.technologies),
                    ", ".join(project.links),
                )
                if part
            )
            for project in profile.projects
        )
        if profile.projects
        else None,
        "certifications": "; ".join(
            " - ".join(part for part in (item.name, item.issuer, item.date) if part)
            for item in profile.certifications
        )
        if profile.certifications
        else None,
        "resume": profile.resume_path,
        "work_authorization": profile.work_authorization,
        "requires_sponsorship": (
            None
            if profile.requires_sponsorship is None
            else "Yes" if profile.requires_sponsorship else "No"
        ),
        "relocation_preference": profile.relocation_preference,
        "notice_period": profile.notice_period,
        "salary_expectation": profile.salary_expectation,
        "employment_type": profile.employment_type,
    }


def map_application_questions(
    form: ApplicationForm,
    profile: ApplicationProfile,
    *,
    application_id: str,
    job: Job,
    review_answers: dict[str, str] | None = None,
) -> ApplicationPayload:
    safe_aliases = {
        alias: key for key, aliases in SAFE_FIELD_ALIASES.items() for alias in aliases
    }
    configured_aliases = {
        alias: key
        for key, aliases in CONFIGURED_FIELD_ALIASES.items()
        for alias in aliases
    }
    configured_answers = {
        _normalize_question(key): value
        for key, value in profile.configured_answers.items()
    }
    human_answers = {
        _normalize_question(key): value.strip()
        for key, value in (review_answers or {}).items()
        if value.strip()
    }
    values = _profile_values(profile)
    configured_field_values = {
        field_key: configured_answers[normalized_alias]
        for field_key, aliases in CONFIGURED_FIELD_ALIASES.items()
        for alias in aliases
        if (normalized_alias := _normalize_question(alias)) in configured_answers
    }
    fields: dict[str, str] = {}
    review_questions: list[ApplicationQuestion] = []
    unanswered_questions: list[ApplicationQuestion] = []
    missing_questions: list[ApplicationQuestion] = []

    for question in form.questions:
        normalized_key = _normalize_question(question.key)
        normalized_label = _normalize_question(question.label)
        field_key = safe_aliases.get(normalized_key) or safe_aliases.get(
            normalized_label
        )
        configured_field = False
        if field_key is None:
            field_key = configured_aliases.get(normalized_key) or configured_aliases.get(
                normalized_label
            )
            configured_field = field_key is not None

        value: str | Path | None = values.get(field_key) if field_key else None
        if field_key in CONFIGURED_FIELD_ALIASES and value is None:
            value = configured_field_values.get(field_key)
        if value is None:
            value = human_answers.get(normalized_key) or human_answers.get(
                normalized_label
            )
        if field_key is None:
            review_questions.append(question)
            if value is None:
                unanswered_questions.append(question)

        if field_key == "resume" and question.question_type == QuestionType.file:
            try:
                value = profile.validate_resume_file()
            except ValueError:
                value = None

        if value is None:
            if field_key is not None and (question.required or configured_field):
                missing_questions.append(question)
            continue

        if question.question_type == QuestionType.file:
            if field_key != "resume" or not isinstance(value, Path):
                review_questions.append(question)
                continue
            try:
                value = profile.validate_resume_file()
            except ValueError:
                missing_questions.append(question)
                continue
        elif isinstance(value, Path):
            review_questions.append(question)
            continue

        fields[question.key] = str(value)

    if form.human_action_required:
        human_action_question = ApplicationQuestion(
            key="human_action",
            label=form.human_action_reason or "Human action required",
        )
        review_questions.append(human_action_question)
        unanswered_questions.append(human_action_question)

    return ApplicationPayload(
        application_id=application_id,
        company=job.company,
        job_title=job.title,
        job_url=job.url,
        application_url=form.application_url,
        platform=form.platform,
        fields=fields,
        review_questions=review_questions,
        unanswered_review_questions=unanswered_questions,
        missing_profile_questions=missing_questions,
    )


class ApplicationResultStatus(StrEnum):
    supported = "supported"
    prepared = "prepared"
    needs_review = "needs_review"
    ready_to_submit = "ready_to_submit"
    submitted = "submitted"
    failed = "failed"
    unsupported = "unsupported"
    human_action_required = "human_action_required"
    unknown_submission_result = "unknown_submission_result"
    duplicate_submission_blocked = "duplicate_submission_blocked"
    retry_not_allowed = "retry_not_allowed"


class ApplicationResult(BaseModel):
    application_id: str
    status: ApplicationResultStatus
    payload: ApplicationPayload | None = None
    attempt: ApplicationAttempt | None = None
    message: str | None = None


class ApplicationEngine:
    def __init__(
        self,
        tracker: ApplicationTracker,
        attempts: AttemptStore,
        adapters: list[ApplicationAdapter] | None = None,
    ) -> None:
        self.tracker = tracker
        self.attempts = attempts
        self.adapters = adapters or []

    def process(
        self,
        application: Application,
        job: Job,
        profile: ApplicationProfile,
        *,
        review_approved: bool = False,
        retry: bool = False,
        review_answers: dict[str, str] | None = None,
    ) -> ApplicationResult:
        persisted_application = self.tracker.get(application.job_url)
        if persisted_application is None:
            raise ValueError(f"Application not found for job URL: {application.job_url}")
        application = persisted_application
        application_id = application_id_for(application.job_url)
        previous = self.attempts.latest_for(application_id)
        if application.status.value == "applied" or (
            previous is not None
            and previous.outcome
            in {
                AttemptOutcome.submitted,
                AttemptOutcome.submitting,
                AttemptOutcome.unknown_submission_result,
            }
        ):
            return ApplicationResult(
                application_id=application_id,
                status=ApplicationResultStatus.duplicate_submission_blocked,
                message="Submission may already have succeeded; automatic retry is blocked.",
            )
        if application.automation_status in {
            AutomationStatus.submitted,
            AutomationStatus.submitting,
            AutomationStatus.unknown_submission_result,
        }:
            return ApplicationResult(
                application_id=application_id,
                status=ApplicationResultStatus.duplicate_submission_blocked,
                message="Application state indicates submission may already have occurred.",
            )
        if application.status.value not in {"discovered", "ready"}:
            return ApplicationResult(
                application_id=application_id,
                status=ApplicationResultStatus.retry_not_allowed,
                message="Application lifecycle status does not allow submission.",
            )
        if previous is not None and previous.outcome == AttemptOutcome.failed:
            if not previous.retryable or not retry:
                return ApplicationResult(
                    application_id=application_id,
                    status=ApplicationResultStatus.retry_not_allowed,
                    message="An explicit retry is allowed only for retryable failures.",
                )

        adapter = next(
            (candidate for candidate in self.adapters if candidate.can_handle(job)),
            None,
        )
        attempt = self.attempts.start(
            application_id=application_id,
            company=application.company,
            job_title=application.job_title,
            job_url=application.job_url,
            platform=adapter.platform if adapter else (job.source or "unknown"),
        )
        if adapter is None:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.unsupported,
            )
            return self._finish(
                attempt,
                AttemptOutcome.unsupported,
                ApplicationResultStatus.unsupported,
                failure_category=FailureCategory.unsupported_platform,
                error_message="No application adapter supports this platform.",
            )

        self.tracker.update_automation_status(
            application.job_url,
            AutomationStatus.preparing,
        )
        try:
            capabilities = adapter.capabilities(job)
        except Exception as exc:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.failed,
            )
            return self._finish(
                attempt,
                AttemptOutcome.failed,
                ApplicationResultStatus.failed,
                failure_category=FailureCategory.adapter_error,
                error_message=str(exc),
            )
        if not capabilities.prepare:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.unsupported,
            )
            return self._finish(
                attempt,
                AttemptOutcome.unsupported,
                ApplicationResultStatus.unsupported,
                failure_category=FailureCategory.unsupported_capability,
                error_message="Adapter does not support application preparation.",
            )
        try:
            form = adapter.prepare(job, profile)
            payload = map_application_questions(
                form,
                profile,
                application_id=application_id,
                job=job,
                review_answers=review_answers,
            )
        except AdapterError as exc:
            return self._handle_adapter_error(attempt, application, exc)
        except Exception as exc:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.failed,
            )
            return self._finish(
                attempt,
                AttemptOutcome.failed,
                ApplicationResultStatus.failed,
                failure_category=FailureCategory.preparation_error,
                error_message=str(exc),
            )

        if form.human_action_required:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.human_action_required,
            )
            return self._finish(
                attempt,
                AttemptOutcome.human_action_required,
                ApplicationResultStatus.human_action_required,
                payload=payload,
                failure_category=FailureCategory.human_action,
                error_message=form.human_action_reason,
            )

        has_file_question = any(
            question.question_type == QuestionType.file
            for question in form.questions
        )
        if has_file_question and not capabilities.resume_upload:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.unsupported,
            )
            return self._finish(
                attempt,
                AttemptOutcome.unsupported,
                ApplicationResultStatus.unsupported,
                payload=payload,
                failure_category=FailureCategory.unsupported_capability,
                error_message="Adapter cannot upload a requested application document.",
            )

        if payload.needs_review or not review_approved:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.needs_review,
            )
            category = (
                FailureCategory.unknown_question
                if payload.review_questions
                else FailureCategory.missing_profile_data
                if payload.missing_profile_questions
                else FailureCategory.review_required
            )
            return self._finish(
                attempt,
                AttemptOutcome.needs_review,
                ApplicationResultStatus.needs_review,
                payload=payload,
                failure_category=category,
                error_message="Application requires review before submission.",
            )

        if payload.fields and not capabilities.fill_known_fields:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.unsupported,
            )
            return self._finish(
                attempt,
                AttemptOutcome.unsupported,
                ApplicationResultStatus.unsupported,
                payload=payload,
                failure_category=FailureCategory.unsupported_capability,
                error_message="Adapter cannot fill mapped application fields.",
            )

        if not capabilities.submit or not capabilities.verify_submission:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.unsupported,
            )
            return self._finish(
                attempt,
                AttemptOutcome.unsupported,
                ApplicationResultStatus.unsupported,
                payload=payload,
                failure_category=FailureCategory.unsupported_capability,
                error_message="Adapter does not support verified submission.",
            )

        self.tracker.update_automation_status(
            application.job_url,
            AutomationStatus.ready_to_submit,
        )
        try:
            fill_result = adapter.fill(payload)
        except AdapterError as exc:
            return self._handle_adapter_error(attempt, application, exc, payload)
        except Exception as exc:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.failed,
            )
            return self._finish(
                attempt,
                AttemptOutcome.failed,
                ApplicationResultStatus.failed,
                payload=payload,
                failure_category=FailureCategory.adapter_error,
                error_message=str(exc),
            )
        if fill_result.human_action_required:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.human_action_required,
            )
            return self._finish(
                attempt,
                AttemptOutcome.human_action_required,
                ApplicationResultStatus.human_action_required,
                payload=payload,
                failure_category=FailureCategory.human_action,
                error_message=fill_result.error_message,
            )
        if not fill_result.success:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.failed,
            )
            return self._finish(
                attempt,
                AttemptOutcome.failed,
                ApplicationResultStatus.failed,
                payload=payload,
                failure_category=fill_result.failure_category or FailureCategory.adapter_error,
                error_message=fill_result.error_message,
                retryable=fill_result.retryable,
            )

        self.tracker.update_automation_status(
            application.job_url,
            AutomationStatus.submitting,
        )
        attempt = self.attempts.update(
            attempt,
            outcome=AttemptOutcome.submitting,
            current_step="submit",
            last_successful_step="fill",
        )
        try:
            receipt = adapter.submit(payload)
            attempt = self.attempts.update(
                attempt,
                current_step="verify",
                submission_outcome=receipt.submission_outcome,
                failure_category=receipt.failure_category,
                error_message=receipt.error_message,
                retryable=receipt.retryable,
            )
            if receipt.human_action_required:
                outcome_is_unknown = receipt.submission_outcome in {
                    SubmissionOutcome.submitted,
                    SubmissionOutcome.unknown,
                }
                self.tracker.update_automation_status(
                    application.job_url,
                    AutomationStatus.unknown_submission_result
                    if outcome_is_unknown
                    else AutomationStatus.human_action_required,
                )
                return self._finish(
                    attempt,
                    AttemptOutcome.unknown_submission_result
                    if outcome_is_unknown
                    else AttemptOutcome.human_action_required,
                    ApplicationResultStatus.unknown_submission_result
                    if outcome_is_unknown
                    else ApplicationResultStatus.human_action_required,
                    payload=payload,
                    failure_category=(
                        FailureCategory.verification_error
                        if outcome_is_unknown
                        else FailureCategory.human_action
                    ),
                    error_message=receipt.error_message,
                    retryable=False,
                    submission_outcome=(
                        SubmissionOutcome.unknown
                        if outcome_is_unknown
                        else receipt.submission_outcome
                    ),
                )
            if receipt.submission_outcome == SubmissionOutcome.unknown:
                self.tracker.update_automation_status(
                    application.job_url,
                    AutomationStatus.unknown_submission_result,
                )
                return self._finish(
                    attempt,
                    AttemptOutcome.unknown_submission_result,
                    ApplicationResultStatus.unknown_submission_result,
                    payload=payload,
                    failure_category=receipt.failure_category,
                    error_message=receipt.error_message,
                    submission_outcome=SubmissionOutcome.unknown,
                )
            if receipt.submission_outcome != SubmissionOutcome.submitted:
                self.tracker.update_automation_status(
                    application.job_url,
                    AutomationStatus.failed,
                )
                return self._finish(
                    attempt,
                    AttemptOutcome.failed,
                    ApplicationResultStatus.failed,
                    payload=payload,
                    failure_category=receipt.failure_category or FailureCategory.submit_error,
                    error_message=receipt.error_message,
                    retryable=receipt.retryable,
                    submission_outcome=SubmissionOutcome.not_submitted,
                )

            verification = adapter.verify_result(payload, receipt)
        except AdapterError as exc:
            return self._handle_adapter_error(attempt, application, exc, payload)
        except Exception as exc:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.unknown_submission_result,
            )
            return self._finish(
                attempt,
                AttemptOutcome.unknown_submission_result,
                ApplicationResultStatus.unknown_submission_result,
                payload=payload,
                failure_category=FailureCategory.verification_error,
                error_message=str(exc),
                submission_outcome=SubmissionOutcome.unknown,
            )

        if verification.outcome == VerificationOutcome.verified:
            if not verification.evidence or not verification.evidence.strip():
                self.tracker.update_automation_status(
                    application.job_url,
                    AutomationStatus.unknown_submission_result,
                )
                return self._finish(
                    attempt,
                    AttemptOutcome.unknown_submission_result,
                    ApplicationResultStatus.unknown_submission_result,
                    payload=payload,
                    failure_category=FailureCategory.verification_error,
                    error_message=(
                        "Adapter reported success without providing verification evidence."
                    ),
                    submission_outcome=SubmissionOutcome.unknown,
                )

            self.tracker.mark_submitted(
                application.job_url,
                submission_evidence=verification.evidence,
            )
            return self._finish(
                attempt,
                AttemptOutcome.submitted,
                ApplicationResultStatus.submitted,
                payload=payload,
                submission_outcome=SubmissionOutcome.submitted,
                verification_result=True,
                verification_evidence=verification.evidence,
            )
        if verification.outcome == VerificationOutcome.not_submitted:
            self.tracker.update_automation_status(
                application.job_url,
                AutomationStatus.failed,
            )
            return self._finish(
                attempt,
                AttemptOutcome.failed,
                ApplicationResultStatus.failed,
                payload=payload,
                failure_category=FailureCategory.verification_error,
                error_message=verification.evidence,
                retryable=receipt.retryable,
                submission_outcome=SubmissionOutcome.not_submitted,
                verification_result=False,
                verification_evidence=verification.evidence,
            )

        self.tracker.update_automation_status(
            application.job_url,
            AutomationStatus.unknown_submission_result,
        )
        return self._finish(
            attempt,
            AttemptOutcome.unknown_submission_result,
            ApplicationResultStatus.unknown_submission_result,
            payload=payload,
            failure_category=FailureCategory.verification_error,
            submission_outcome=SubmissionOutcome.unknown,
            verification_evidence=verification.evidence,
        )

    def _handle_adapter_error(
        self,
        attempt: ApplicationAttempt,
        application: Application,
        error: AdapterError,
        payload: ApplicationPayload | None = None,
    ) -> ApplicationResult:
        unknown = error.submission_started
        self.tracker.update_automation_status(
            application.job_url,
            AutomationStatus.unknown_submission_result if unknown else AutomationStatus.failed,
        )
        return self._finish(
            attempt,
            AttemptOutcome.unknown_submission_result if unknown else AttemptOutcome.failed,
            ApplicationResultStatus.unknown_submission_result if unknown else ApplicationResultStatus.failed,
            payload=payload,
            failure_category=FailureCategory.adapter_error,
            error_message=str(error),
            retryable=error.retryable and not unknown,
            submission_outcome=(
                SubmissionOutcome.unknown if unknown else SubmissionOutcome.not_started
            ),
        )

    def _finish(
        self,
        attempt: ApplicationAttempt,
        outcome: AttemptOutcome,
        status: ApplicationResultStatus,
        *,
        payload: ApplicationPayload | None = None,
        failure_category: FailureCategory | None = None,
        error_message: str | None = None,
        retryable: bool = False,
        submission_outcome: SubmissionOutcome | None = None,
        verification_result: bool | None = None,
        verification_evidence: str | None = None,
    ) -> ApplicationResult:
        finished = self.attempts.update(
            attempt,
            outcome=outcome,
            completed_at=datetime.now(timezone.utc),
            current_step=outcome.value,
            failure_category=failure_category,
            error_message=error_message,
            retryable=retryable,
            submission_outcome=submission_outcome,
            verification_result=verification_result,
            verification_evidence=verification_evidence,
            review_questions=(
                [
                    ReviewQuestionSnapshot(
                        key=question.key,
                        label=question.label,
                        required=question.required,
                        question_type=question.question_type.value,
                    )
                    for question in payload.review_questions
                ]
                if payload is not None
                else None
            ),
            missing_profile_fields=(
                [question.key for question in payload.missing_profile_questions]
                if payload is not None
                else None
            ),
        )
        return ApplicationResult(
            application_id=attempt.application_id,
            status=status,
            payload=payload,
            attempt=finished,
            message=error_message,
        )

    def resolve_unknown_submission(
        self,
        job_url: str,
        *,
        submitted: bool,
        evidence: str,
    ) -> ApplicationResult:
        if not evidence.strip():
            raise ValueError("Reconciliation evidence must not be empty")

        application = self.tracker.get(job_url)
        if application is None:
            raise ValueError(f"Application not found for job URL: {job_url}")

        application_id = application_id_for(application.job_url)
        attempt = self.attempts.latest_for(application_id)
        if (
            attempt is None
            or attempt.outcome != AttemptOutcome.unknown_submission_result
        ):
            raise ValueError("Application has no unknown submission attempt to resolve")

        application = self.tracker.resolve_unknown_submission(
            job_url,
            submitted=submitted,
            evidence=evidence,
        )
        updated_attempt = self.attempts.update(
            attempt,
            outcome=(AttemptOutcome.submitted if submitted else AttemptOutcome.failed),
            completed_at=datetime.now(timezone.utc),
            current_step="manual_reconciliation",
            retryable=not submitted,
            submission_outcome=(
                SubmissionOutcome.submitted
                if submitted
                else SubmissionOutcome.not_submitted
            ),
            verification_result=submitted,
            reconciliation_evidence=evidence.strip(),
        )
        return ApplicationResult(
            application_id=application_id,
            status=(
                ApplicationResultStatus.submitted
                if submitted
                else ApplicationResultStatus.failed
            ),
            attempt=updated_attempt,
            message="Manually reconciled using supplied evidence.",
        )
