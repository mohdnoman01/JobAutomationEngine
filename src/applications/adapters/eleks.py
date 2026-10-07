from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from src.applications.adapters.base import (
    AdapterField,
    AdapterSubmissionResult,
    AdapterVerificationResult,
    ApplicationAdapter,
    InspectionResult,
    PreparationResult,
)
from src.applications.adapters.browser import BrowserAutomation, BrowserField, BrowserPageSnapshot
from src.applications.models import Application
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
    """Read-only ELEKS form adapter; submission is deliberately disabled."""

    platform = "eleks_gravity_forms"

    def __init__(self, browser: BrowserAutomation) -> None:
        self.browser = browser
        self._preparation_statuses: dict[str, ApplicationResultStatus] = {}

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
                if field.kind.casefold() == "file" and not field.honeypot
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
        return result

    def submit(self, application: Application) -> AdapterSubmissionResult:
        previous_status = self._preparation_statuses.get(
            normalize_job_url(application.job_url)
        )
        if previous_status == ApplicationResultStatus.needs_review:
            return AdapterSubmissionResult(
                status=ApplicationResultStatus.needs_review,
                message="Submission is blocked while application review is required.",
            )

        return AdapterSubmissionResult(
            status=ApplicationResultStatus.unsupported,
            receipt=SubmissionReceipt(
                submission_outcome=SubmissionOutcome.not_started,
                error_message="ELEKS submission is intentionally not implemented.",
            ),
            message="ELEKS submission is disabled; no Send control was activated.",
        )

    def verify_result(self, application: Application) -> AdapterVerificationResult:
        return AdapterVerificationResult(
            status=ApplicationResultStatus.unknown_submission_result,
            verification=SubmissionVerification(outcome=VerificationOutcome.unknown),
            message="No submission was performed, so there is no result to verify.",
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
