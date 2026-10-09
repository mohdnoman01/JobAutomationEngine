from __future__ import annotations

from src.applications.adapters.eleks import EleksAdapter
from src.applications.submission import (
    AdapterError,
    ApplicationCapabilities,
    ApplicationForm,
    ApplicationPayload,
    ApplicationAdapter,
    FillResult,
    ReviewApproval,
    SubmissionReceipt,
    SubmissionVerification,
    VerificationOutcome,
)
from src.applications.models import Application
from src.applications.profile import ApplicationProfile
from src.applications.recovery import SubmissionOutcome
from src.research.job_normalizer import normalize_job_url
from src.research.models import Job


class EleksEngineBridge(ApplicationAdapter):
    """Adapt ELEKS preparation and evidence to the engine contract.

    The concrete browser remains behind BrowserAutomation. A bridge reaches
    the platform submission boundary only with a matching ReviewApproval.
    """

    platform = EleksAdapter.platform

    def __init__(self, platform_adapter: EleksAdapter) -> None:
        self.platform_adapter = platform_adapter
        self._application: Application | None = None

    def can_handle(self, job: Job) -> bool:
        return self.platform_adapter.can_handle(job)

    def capabilities(self, job: Job) -> ApplicationCapabilities:
        return ApplicationCapabilities(
            prepare=True,
            fill_known_fields=True,
            resume_upload=True,
            submit=True,
            verify_submission=True,
        )

    def prepare(self, job: Job, profile: ApplicationProfile) -> ApplicationForm:
        self._application = Application(
            company=job.company,
            job_title=job.title,
            job_url=job.url,
        )
        prepared = self.platform_adapter.prepare(self._application, profile)
        inspection = prepared.inspection
        if prepared.status.value == "human_action_required":
            form = inspection.form if inspection and inspection.form else ApplicationForm(
                platform=self.platform,
                application_url=job.url,
                human_action_required=True,
                human_action_reason=prepared.message,
            )
            form.human_action_required = True
            form.human_action_reason = prepared.message
            return form
        if inspection is None or inspection.form is None:
            raise AdapterError(
                prepared.message or "ELEKS preparation did not produce an inspectable form.",
                submission_started=False,
            )
        form = inspection.form.model_copy(deep=True)
        return form

    def fill(self, payload: ApplicationPayload) -> FillResult:
        # ELEKS preparation maps and fills only its allow-listed safe fields.
        prepared = (
            self.platform_adapter.preparation_result(self._application)
            if self._application
            else None
        )
        if prepared is None or prepared.payload is None:
            return FillResult(
                success=False,
                error_message="ELEKS safe-field preparation was not completed.",
            )
        return FillResult()

    def submit(
        self,
        payload: ApplicationPayload,
        *,
        approval: ReviewApproval | None = None,
    ) -> SubmissionReceipt:
        if self._application is None or approval is None or not approval.validates(
            payload, self._application.job_url
        ):
            return SubmissionReceipt(
                submission_outcome=SubmissionOutcome.not_started,
                error_message="A matching payload-bound approval is required.",
            )
        prepared = self.platform_adapter.preparation_result(self._application)
        if prepared is None or prepared.payload is None:
            return SubmissionReceipt(
                submission_outcome=SubmissionOutcome.not_started,
                error_message="ELEKS preparation is unavailable.",
            )
        platform_payload = prepared.payload.model_copy(deep=True)
        platform_fields = dict(platform_payload.fields)
        engine_fields = dict(payload.fields)
        # ELEKS preparation records its unchecked vacancy-updates control as
        # false even though the generic profile mapper has no checkbox field.
        if platform_fields.get("updates") == "false" and "updates" not in engine_fields:
            platform_fields.pop("updates")
        if platform_fields != engine_fields:
            return SubmissionReceipt(
                submission_outcome=SubmissionOutcome.not_started,
                error_message=(
                    "The engine-approved fields do not match the prepared ELEKS fields; "
                    "submission is blocked."
                ),
            )
        normalized_platform_payload = platform_payload.model_copy(
            update={"fields": platform_fields}
        )
        if normalized_platform_payload.fingerprint() != payload.fingerprint():
            return SubmissionReceipt(
                submission_outcome=SubmissionOutcome.not_started,
                error_message=(
                    "The engine-approved payload does not match the prepared ELEKS payload; "
                    "submission is blocked."
                ),
            )
        platform_approval = ReviewApproval.approve(
            prepared.payload,
            job_url=self._application.job_url,
            acknowledged_question_keys=set(approval.acknowledged_question_keys),
        )
        result = self.platform_adapter.submit(
            self._application,
            approval=platform_approval,
            attempt_claimed=True,
        )
        if result.receipt is None:
            raise AdapterError(result.message or "ELEKS submission returned no receipt.")
        return result.receipt

    def verify_result(
        self,
        payload: ApplicationPayload,
        receipt: SubmissionReceipt,
        *,
        attempt,
    ) -> SubmissionVerification:
        if self._application is None:
            return SubmissionVerification(outcome=VerificationOutcome.unknown)
        result = self.platform_adapter.verify_result(self._application)
        verification = result.verification
        if result.evidence is not None and verification.outcome == VerificationOutcome.verified:
            verification = verification.model_copy(
                update={"evidence": verification.evidence or result.evidence.model_dump_json()}
            )
        if verification.outcome == VerificationOutcome.verified and (
            verification.application_id != attempt.application_id
            or verification.job_url is None
            or normalize_job_url(verification.job_url) != normalize_job_url(attempt.job_url)
            or verification.attempt_number != attempt.attempt_number
        ):
            return SubmissionVerification(
                outcome=VerificationOutcome.unknown,
                evidence="Adapter confirmation evidence is not bound to the current engine attempt.",
            )
        return verification
