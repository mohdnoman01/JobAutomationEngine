from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from src.applications.adapters.base import (
    AdapterField,
    AdapterSubmissionResult,
    AdapterVerificationResult,
    ApplicationAdapter,
    InspectionResult,
    PreparationResult,
)
from src.applications.adapters.browser import (
    BrowserAutomation,
    BrowserField,
    BrowserHumanActionRequired,
    BrowserPageSnapshot,
    BrowserSubmissionAuthorization,
    BrowserSubmissionOutcome,
)
from src.applications.adapters.verification import (
    SubmissionVerifier,
    VisibleConfirmationTextVerifier,
)
from src.applications.models import Application
from src.applications.recovery import AttemptOutcome, AttemptStore, SubmissionOutcome as StoredSubmissionOutcome
from src.applications.profile import ApplicationProfile
from src.applications.submission import (
    ApplicationForm,
    ApplicationPayload,
    ApplicationQuestion,
    ApplicationResultStatus,
    QuestionType,
    SubmissionOutcome,
    SubmissionReceipt,
    SubmissionVerification,
    VerificationOutcome,
    ReviewApproval,
    application_id_for,
    map_application_questions,
)
from src.research.job_normalizer import normalize_job_url
from src.research.models import Job


ELEKS_HOST = "careers.eleks.com"
ELEKS_VACANCY_PREFIX = "/vacancies/"
ELEKS_RESUME_EXTENSIONS = frozenset(
    {".jpg", ".gif", ".png", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
)
ELEKS_MAX_RESUME_BYTES = 10 * 1024 * 1024
VACANCY_UPDATE_LABEL = "get updated about new vacancies at eleks"


def _normalize_label(value: str) -> str:
    return " ".join(
        value.casefold().replace("*", " ").replace("_", " ").split()
    )


def _is_vacancy_updates(field: BrowserField) -> bool:
    return field.kind.casefold() == "checkbox" and (
        _normalize_label(field.label) == VACANCY_UPDATE_LABEL
    )


def _is_honeypot(field: BrowserField) -> bool:
    return field.honeypot or field.kind.casefold() == "honeypot"


class EleksAdapter(ApplicationAdapter):
    """ELEKS form adapter with preparation and explicitly approved submission."""

    platform = "eleks_gravity_forms"

    def __init__(
        self,
        browser: BrowserAutomation,
        *,
        verifier: SubmissionVerifier | None = None,
        attempt_store: AttemptStore | None = None,
    ) -> None:
        self.browser = browser
        self.verifier = verifier or VisibleConfirmationTextVerifier(
            browser,
            confirmation_marker=None,
        )
        self._preparation_statuses: dict[str, ApplicationResultStatus] = {}
        self._preparation_results: dict[str, PreparationResult] = {}
        self._verification_results: dict[str, AdapterVerificationResult] = {}
        self.attempt_store = attempt_store or AttemptStore()

    def can_handle(self, job: Job) -> bool:
        parsed = urlsplit(job.url)
        return (
            parsed.hostname is not None
            and parsed.hostname.casefold() == ELEKS_HOST
            and parsed.path.startswith(ELEKS_VACANCY_PREFIX)
        )

    def inspect(self, job: Job) -> InspectionResult:
        try:
            return self._inspect(job)
        except BrowserHumanActionRequired as exc:
            return self._human_action(str(exc))
        except Exception as exc:
            return InspectionResult(
                status=ApplicationResultStatus.failed,
                platform=self.platform,
                application_url=job.url,
                message=f"ELEKS form inspection failed: {exc}",
            )

    def _inspect(self, job: Job) -> InspectionResult:
        if not self.can_handle(job):
            return InspectionResult(
                status=ApplicationResultStatus.unsupported,
                platform=self.platform,
                message="Job URL is not an ELEKS vacancy page.",
            )

        self.browser.open_url(job.url)
        challenge = self.browser.detect_human_action()
        if challenge:
            return self._human_action(challenge)

        snapshot = self.browser.inspect_page()
        challenge = self.browser.detect_human_action()
        if challenge:
            return self._human_action(challenge, snapshot)

        if not snapshot.form_found:
            apply_control = next(
                (
                    control
                    for control in snapshot.controls
                    if _normalize_label(control.label) in {"apply", "apply now"}
                    and control.safe_to_click
                ),
                None,
            )
            if apply_control is None:
                return InspectionResult(
                    status=ApplicationResultStatus.unsupported,
                    platform=self.platform,
                    application_url=snapshot.url,
                    message="No application form or safe Apply control was found.",
                )

            self.browser.click_safe_control(apply_control.selector)
            challenge = self.browser.detect_human_action()
            if challenge:
                return self._human_action(challenge, self.browser.inspect_page())
            snapshot = self.browser.inspect_page()

        if not snapshot.form_found:
            return InspectionResult(
                status=ApplicationResultStatus.unsupported,
                platform=self.platform,
                application_url=snapshot.url,
                message="The Apply control did not reveal a supported application form.",
            )

        application_url = snapshot.url or job.url
        form_action = urljoin(application_url, snapshot.form_action or application_url)
        if not self._same_origin(application_url, form_action):
            return InspectionResult(
                status=ApplicationResultStatus.unsupported,
                platform=self.platform,
                application_url=application_url,
                message="The form posts to a different origin; this adapter will not follow it.",
            )

        visible_fields = [field for field in snapshot.fields if not _is_honeypot(field)]
        canonical_labels = {
            _normalize_label(field.label)
            for field in visible_fields
            if not _is_vacancy_updates(field)
        }
        required_contact_fields = {"full name", "email", "phone", "attach a cv"}
        if not required_contact_fields.issubset(canonical_labels):
            return InspectionResult(
                status=ApplicationResultStatus.unsupported,
                platform=self.platform,
                application_url=application_url,
                fields=[self._adapter_field(field) for field in snapshot.fields],
                message="The required ELEKS application fields differ from the inspected form.",
            )

        questions = [
            ApplicationQuestion(
                key=field.key,
                label=field.label,
                required=field.required,
                question_type=(
                    QuestionType.file
                    if field.kind.casefold() == "file"
                    else QuestionType.text
                ),
            )
            for field in visible_fields
            if not _is_vacancy_updates(field)
        ]
        form = ApplicationForm(
            platform=self.platform,
            application_url=application_url,
            questions=questions,
        )
        return InspectionResult(
            status=ApplicationResultStatus.supported,
            platform=self.platform,
            application_url=application_url,
            fields=[self._adapter_field(field) for field in snapshot.fields],
            form=form,
            message="ELEKS Gravity Forms application form inspected; no submission performed.",
        )

    def prepare(
        self,
        application: Application,
        profile: ApplicationProfile,
    ) -> PreparationResult:
        application_key = normalize_job_url(application.job_url)
        self._preparation_results.pop(application_key, None)
        self._verification_results.pop(application_key, None)
        job = Job(
            title=application.job_title,
            company=application.company,
            url=application.job_url,
        )
        inspection = self.inspect(job)
        application_id = application_id_for(application.job_url)

        if inspection.status != ApplicationResultStatus.supported or inspection.form is None:
            result = PreparationResult(
                status=inspection.status,
                inspection=inspection,
                message=inspection.message,
            )
            self._preparation_statuses[normalize_job_url(application.job_url)] = result.status
            return result

        file_field = next(
            (
                field
                for field in inspection.fields
                if field.kind.casefold() == "file"
                and _normalize_label(field.label) in {"attach a cv", "cv", "resume", "attach cv"}
                and not field.honeypot
            ),
            None,
        )
        if profile.resume_path is not None and file_field is not None:
            try:
                resume_path = profile.validate_resume_file()
                resume_size = resume_path.stat().st_size
            except ValueError as exc:
                return self._preparation_failure(inspection, str(exc))
            except OSError as exc:
                return self._preparation_failure(
                    inspection,
                    f"Configured CV could not be inspected: {exc}",
                )
            if resume_path.suffix.casefold() not in ELEKS_RESUME_EXTENSIONS:
                return self._preparation_failure(
                    inspection,
                    "Configured CV file type is not accepted by the ELEKS form.",
                )
            max_size = file_field.max_file_size or ELEKS_MAX_RESUME_BYTES
            if resume_size > max_size:
                return self._preparation_failure(
                    inspection,
                    "Configured CV exceeds the ELEKS form's 10 MB limit.",
                )

        payload = map_application_questions(
            inspection.form,
            profile,
            application_id=application_id,
            job=job,
        )
        if file_field is not None and file_field.key in payload.fields:
            payload.fields[file_field.key] = str(profile.validate_resume_file())

        update_field = next(
            (field for field in inspection.fields if _is_vacancy_updates(field)),
            None,
        )
        if update_field is not None:
            configured_value = next(
                (
                    value
                    for key, value in profile.configured_answers.items()
                    if _normalize_label(key) in {
                        VACANCY_UPDATE_LABEL,
                        "eleks vacancy updates",
                    }
                ),
                None,
            )
            if configured_value is None:
                payload.fields[update_field.key] = "false"
            elif configured_value.strip().casefold() in {"true", "yes", "1", "on"}:
                payload.fields[update_field.key] = "true"
            elif configured_value.strip().casefold() in {"false", "no", "0", "off"}:
                payload.fields[update_field.key] = "false"
            else:
                question = ApplicationQuestion(
                    key=update_field.key,
                    label=update_field.label,
                    required=False,
                )
                payload.review_questions.append(question)
                payload.unanswered_review_questions.append(question)

        try:
            self._apply_prepared_values(inspection, payload)
        except BrowserHumanActionRequired as exc:
            human_inspection = self._human_action(str(exc))
            result = PreparationResult(
                status=ApplicationResultStatus.human_action_required,
                inspection=human_inspection,
                payload=payload,
                message=str(exc),
            )
            self._preparation_statuses[normalize_job_url(application.job_url)] = result.status
            return result
        except Exception as exc:
            result = PreparationResult(
                status=ApplicationResultStatus.failed,
                inspection=inspection,
                payload=payload,
                message=f"ELEKS form preparation failed: {exc}",
            )
            self._preparation_statuses[normalize_job_url(application.job_url)] = result.status
            return result

        status = (
            ApplicationResultStatus.needs_review
            if payload.needs_review
            else ApplicationResultStatus.ready_to_submit
        )
        result = PreparationResult(
            status=status,
            inspection=inspection,
            payload=payload,
            message=(
                "Message and any missing required data require user review."
                if payload.needs_review
                else "Known fields prepared; sending remains disabled."
            ),
        )
        self._preparation_statuses[normalize_job_url(application.job_url)] = status
        self._preparation_results[normalize_job_url(application.job_url)] = result
        return result

    def preparation_result(self, application: Application) -> PreparationResult | None:
        """Return the current preparation snapshot for the supplied application."""
        return self._preparation_results.get(normalize_job_url(application.job_url))

    def _apply_prepared_values(
        self,
        inspection: InspectionResult,
        payload: ApplicationPayload,
    ) -> None:
        safe_text_labels = {
            "full name",
            "name",
            "email",
            "email address",
            "phone",
            "phone number",
            "telephone",
        }
        safe_file_labels = {"attach a cv", "cv", "resume", "attach cv"}
        update_field = next(
            (
                field
                for field in inspection.fields
                if field.kind.casefold() == "checkbox"
                and _normalize_label(field.label) == VACANCY_UPDATE_LABEL
                and not field.honeypot
            ),
            None,
        )

        actions: list[tuple[str, str, str | bool]] = []
        for field in inspection.fields:
            if field.honeypot:
                continue
            label = _normalize_label(field.label)
            if field.kind.casefold() == "file" and label in safe_file_labels:
                if field.key in payload.fields:
                    actions.append(("upload", field.key, payload.fields[field.key]))
            elif (
                field.kind.casefold() == "checkbox"
                and update_field is not None
                and field.key == update_field.key
            ):
                if field.key in payload.fields:
                    actions.append(
                        ("checkbox", field.key, payload.fields[field.key].casefold() == "true")
                    )
            elif field.kind.casefold() != "file" and label in safe_text_labels:
                if field.key in payload.fields:
                    actions.append(("fill", field.key, payload.fields[field.key]))

        for action, key, value in actions:
            self._stop_if_human_action_required()
            if action == "upload":
                self.browser.upload_file(key, str(value))
            elif action == "checkbox":
                self.browser.set_checkbox(key, bool(value))
            else:
                self.browser.fill_field(key, str(value))
            self._stop_if_human_action_required()

    def _stop_if_human_action_required(self) -> None:
        challenge = self.browser.detect_human_action()
        if challenge:
            raise BrowserHumanActionRequired(challenge)

    def submit(
        self,
        application: Application,
        *,
        approval: ReviewApproval | None = None,
        attempt_claimed: bool = False,
        authorization_payload: ApplicationPayload | None = None,
        authorization_approval: ReviewApproval | None = None,
    ) -> AdapterSubmissionResult:
        application_key = normalize_job_url(application.job_url)
        preparation = self._preparation_results.get(application_key)
        if (
            preparation is None
            or preparation.status
            not in {
                ApplicationResultStatus.needs_review,
                ApplicationResultStatus.ready_to_submit,
            }
            or preparation.payload is None
        ):
            return self._not_started(
                ApplicationResultStatus.unsupported,
                "A successful ELEKS preparation is required before submission.",
            )
        try:
            challenge = self.browser.detect_human_action()
        except Exception as exc:
            return self._not_started(
                ApplicationResultStatus.failed,
                f"Browser state failed before submission: {exc}",
            )
        if challenge:
            return AdapterSubmissionResult(
                status=ApplicationResultStatus.human_action_required,
                receipt=SubmissionReceipt(
                    submission_outcome=SubmissionOutcome.not_started,
                    error_message=challenge,
                    human_action_required=True,
                ),
                message=challenge,
            )
        if approval is None:
            return AdapterSubmissionResult(
                status=ApplicationResultStatus.needs_review,
                receipt=SubmissionReceipt(
                    submission_outcome=SubmissionOutcome.not_started,
                    error_message="Explicit payload-bound human approval is required before submission.",
                ),
                message="Explicit payload-bound human approval is required before submission.",
            )
        if not approval.validates(preparation.payload, application.job_url):
            return self._not_started(
                ApplicationResultStatus.needs_review,
                "Approval does not match the prepared ELEKS payload and application.",
            )

        authorized_payload = authorization_payload or preparation.payload
        authorized_approval = authorization_approval or approval
        if not authorized_approval.validates(authorized_payload, application.job_url):
            return self._not_started(
                ApplicationResultStatus.needs_review,
                "Browser authorization does not match the approved payload.",
            )
        if preparation.payload.missing_profile_questions:
            return self._not_started(
                ApplicationResultStatus.needs_review,
                "Required profile fields are missing; submission remains blocked.",
            )
        unresolved = {
            question.key for question in preparation.payload.unanswered_review_questions
        }
        if not unresolved.issubset(approval.acknowledged_question_keys):
            return self._not_started(
                ApplicationResultStatus.needs_review,
                "Unresolved application questions require explicit acknowledgement.",
            )

        claim = None
        if attempt_claimed and not self.attempt_store.has_submission_in_progress(
            preparation.payload.application_id
        ):
            return self._not_started(
                ApplicationResultStatus.duplicate_submission_blocked,
                "The engine has not persisted an active submission claim.",
            )
        if attempt_claimed:
            claim = self.attempt_store.latest_for(preparation.payload.application_id)
            if (
                claim is None
                or claim.payload_fingerprint != authorized_payload.fingerprint()
            ):
                return self._not_started(
                    ApplicationResultStatus.duplicate_submission_blocked,
                    "The persistent claim does not match the authorized payload.",
                )
        if not attempt_claimed:
            claim = self.attempt_store.claim_submission(
                application_id=preparation.payload.application_id,
                company=application.company,
                job_title=application.job_title,
                job_url=application.job_url,
                platform=self.platform,
                payload_fingerprint=authorized_payload.fingerprint(),
            )
            if claim is None:
                return self._not_started(
                    ApplicationResultStatus.duplicate_submission_blocked,
                    "A persisted submission attempt already exists; duplicate submission is blocked.",
                )
        try:
            browser_result = self.browser.submit_form(
                authorization=BrowserSubmissionAuthorization(
                    approval=authorized_approval,
                    payload=authorized_payload,
                    attempt=claim,
                    attempt_store=self.attempt_store,
                )
            )
        except Exception as exc:
            # A port exception does not prove that the browser action never began.
            return self._verify_started_submission(
                application_key,
                attempt_message=f"Browser submit outcome is ambiguous: {exc}",
                attempt=claim,
            )

        if not browser_result.action_started:
            if browser_result.human_action_required:
                return AdapterSubmissionResult(
                    status=ApplicationResultStatus.human_action_required,
                    receipt=SubmissionReceipt(
                        submission_outcome=SubmissionOutcome.not_started,
                        error_message=browser_result.message,
                        human_action_required=True,
                    ),
                    message=browser_result.message,
                )
            if browser_result.outcome == BrowserSubmissionOutcome.unknown:
                return self._unknown_submission(browser_result.message)
            if claim is not None:
                self.attempt_store.update(
                    claim,
                    outcome=AttemptOutcome.failed,
                    submission_outcome=StoredSubmissionOutcome.not_submitted,
                    retryable=False,
                    completed_at=datetime.now(timezone.utc),
                )
            return self._not_started(
                ApplicationResultStatus.failed,
                browser_result.message or "The browser did not start submission.",
            )

        return self._verify_started_submission(
            application_key,
            attempt_message=browser_result.message,
            attempt=claim,
        )

    def _verify_started_submission(
        self,
        application_key: str,
        *,
        attempt_message: str | None,
        attempt=None,
    ) -> AdapterSubmissionResult:
        try:
            if attempt is None:
                attempt = self.attempt_store.latest_for(
                    self._preparation_results[application_key].payload.application_id
                )
            if attempt is None:
                raise ValueError("No persisted application attempt is available for verification.")
            verification = self.verifier.verify(attempt=attempt)
        except Exception as exc:
            verification = AdapterVerificationResult(
                status=ApplicationResultStatus.unknown_submission_result,
                verification=SubmissionVerification(
                    outcome=VerificationOutcome.unknown,
                    evidence=f"Submission verification failed: {exc}",
                ),
                message=f"Submission verification failed: {exc}",
            )
        self._verification_results[application_key] = verification

        attempt_context_matches = (
            attempt is not None
            and verification.verification.application_id == attempt.application_id
            and verification.verification.job_url is not None
            and normalize_job_url(verification.verification.job_url)
            == normalize_job_url(attempt.job_url)
            and verification.verification.attempt_number == attempt.attempt_number
            and verification.verification.verified_evidence is not None
            and verification.verification.verified_evidence.validates_for(
                application_id=attempt.application_id,
                job_url=attempt.job_url,
                attempt_number=attempt.attempt_number,
                payload_fingerprint=attempt.payload_fingerprint or "",
            )
        )
        if (
            verification.verification.outcome == VerificationOutcome.verified
            and verification.verification.evidence
            and verification.verification.evidence.strip()
            and attempt_context_matches
        ):
            self.attempt_store.update(
                attempt,
                outcome=AttemptOutcome.submitted,
                submission_outcome=StoredSubmissionOutcome.submitted,
                verification_result=True,
                verification_evidence=verification.verification.evidence,
                completed_at=datetime.now(timezone.utc),
            )
            return AdapterSubmissionResult(
                status=ApplicationResultStatus.submitted,
                receipt=SubmissionReceipt(
                    submission_outcome=SubmissionOutcome.submitted,
                ),
                message=verification.message,
            )
        if attempt is not None:
            self.attempt_store.update(
                attempt,
                outcome=AttemptOutcome.unknown_submission_result,
                submission_outcome=StoredSubmissionOutcome.unknown,
                retryable=False,
                completed_at=datetime.now(timezone.utc),
            )
        return self._unknown_submission(
            attempt_message or verification.message
        )

    def verify_result(self, application: Application) -> AdapterVerificationResult:
        return self._verification_results.get(
            normalize_job_url(application.job_url),
            AdapterVerificationResult(
                status=ApplicationResultStatus.unknown_submission_result,
                verification=SubmissionVerification(
                    outcome=VerificationOutcome.unknown,
                    evidence="No approved submission attempt is available to verify.",
                ),
                message="No approved submission attempt is available to verify.",
            ),
        )

    @staticmethod
    def _not_started(
        status: ApplicationResultStatus,
        message: str,
    ) -> AdapterSubmissionResult:
        return AdapterSubmissionResult(
            status=status,
            receipt=SubmissionReceipt(
                submission_outcome=SubmissionOutcome.not_started,
                error_message=message,
            ),
            message=message,
        )

    @staticmethod
    def _unknown_submission(message: str | None) -> AdapterSubmissionResult:
        explanation = message or "Submission may have started, but its outcome is unknown."
        return AdapterSubmissionResult(
            status=ApplicationResultStatus.unknown_submission_result,
            receipt=SubmissionReceipt(
                submission_outcome=SubmissionOutcome.unknown,
                error_message=explanation,
                retryable=False,
            ),
            message=explanation,
        )

    def _human_action(
        self,
        reason: str,
        snapshot: BrowserPageSnapshot | None = None,
    ) -> InspectionResult:
        return InspectionResult(
            status=ApplicationResultStatus.human_action_required,
            platform=self.platform,
            application_url=snapshot.url if snapshot is not None else None,
            fields=(
                [self._adapter_field(field) for field in snapshot.fields]
                if snapshot is not None
                else []
            ),
            message=reason,
        )

    @staticmethod
    def _adapter_field(field: BrowserField) -> AdapterField:
        return AdapterField(
            key=field.key,
            label=field.label,
            kind=field.kind,
            required=field.required,
            accept=field.accept,
            max_file_size=field.max_file_size,
            honeypot=_is_honeypot(field),
        )

    @staticmethod
    def _same_origin(first_url: str, second_url: str) -> bool:
        first = urlsplit(first_url)
        second = urlsplit(second_url)
        return (first.scheme.casefold(), first.netloc.casefold()) == (
            second.scheme.casefold(),
            second.netloc.casefold(),
        )

    def _preparation_failure(
        self,
        inspection: InspectionResult,
        message: str,
    ) -> PreparationResult:
        result = PreparationResult(
            status=ApplicationResultStatus.needs_review,
            inspection=inspection,
            message=message,
        )
        if inspection.application_url:
            self._preparation_statuses[normalize_job_url(inspection.application_url)] = result.status
        return result
