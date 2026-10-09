from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.applications.models import Application, ApplicationStatus, AutomationStatus
from src.applications.profile import ApplicationProfile, EducationEntry
from src.applications.recovery import (
    ApplicationAttempt,
    AttemptOutcome,
    AttemptStore,
    FailureCategory,
    RecoveryQueue,
    SubmissionOutcome,
)
from src.applications.submission import (
    AdapterError,
    ApplicationCapabilities,
    ApplicationEngine,
    ApplicationForm,
    ApplicationQuestion,
    ApplicationResultStatus,
    FillResult,
    QuestionType,
    ReviewApproval,
    SubmissionReceipt,
    SubmissionVerification,
    VerificationOutcome,
    application_id_for,
    map_application_questions,
)
from src.applications.tracker import ApplicationTracker
from src.research.models import Job


def make_job() -> Job:
    return Job(
        title="Android Engineer",
        company="Test Startup",
        url="https://example.com/jobs/android",
        source="test_platform",
    )


def make_form(*questions: ApplicationQuestion) -> ApplicationForm:
    return ApplicationForm(
        platform="test_platform",
        application_url="https://example.com/jobs/android/apply",
        questions=list(questions),
    )


class FakeAdapter:
    platform = "test_platform"

    def __init__(self, form=None, receipt=None, verification=None, error=None):
        self.fixture_verification = verification is None
        self.form = form or make_form(
            ApplicationQuestion(key="name", label="Full Name"),
        )
        self.receipt = receipt or SubmissionReceipt(
            submission_outcome=SubmissionOutcome.submitted,
        )
        self.verification = verification or SubmissionVerification(
            outcome=VerificationOutcome.verified,
            evidence="Confirmation reference: test-only",
        )
        self.error = error
        self.submit_calls = 0
        self.fill_calls = 0

    def can_handle(self, job):
        return job.source == self.platform

    def capabilities(self, job):
        return ApplicationCapabilities(
            fill_known_fields=True,
            submit=True,
            verify_submission=True,
        )

    def prepare(self, job, profile):
        return self.form

    def submit(self, payload, *, approval=None):
        self.submit_calls += 1
        if self.error:
            raise self.error
        return self.receipt

    def fill(self, payload):
        self.fill_calls += 1
        return FillResult()

    def verify_result(self, payload, receipt, *, attempt):
        if self.fixture_verification:
            return self.verification.model_copy(
                update={
                    "application_id": attempt.application_id,
                    "job_url": attempt.job_url,
                    "attempt_number": attempt.attempt_number,
                }
            )
        return self.verification


def make_engine(tmp_path, adapter=None):
    tracker = ApplicationTracker(tmp_path / "applications.json")
    attempts = AttemptStore(tmp_path / "attempts.json")
    application = tracker.create(
        company="Test Startup",
        job_title="Android Engineer",
        job_url="https://example.com/jobs/android",
    )
    engine = ApplicationEngine(
        tracker,
        attempts,
        adapters=[adapter] if adapter else [],
    )
    return tracker, attempts, application, engine


def make_approval(adapter, job, profile, *, review_answers=None):
    form = adapter.prepare(job, profile)
    payload = map_application_questions(
        form,
        profile,
        application_id=application_id_for(job.url),
        job=job,
        review_answers=review_answers,
    )
    return ReviewApproval.approve(
        payload,
        job_url=job.url,
        acknowledged_question_keys={
            question.key for question in payload.unanswered_review_questions
        },
    )


def test_application_profile_supports_structured_data_and_legacy_preferences():
    profile = ApplicationProfile(
        name="Test Person",
        email="person@example.com",
        education=[EducationEntry(degree="BSc", institution="Example University")],
        skills=["Python", "Kotlin"],
        configured_answers={"Will you need sponsorship?": "No"},
    )

    assert profile.education[0].degree == "BSc"
    assert profile.skills == ["Python", "Kotlin"]
    assert ApplicationProfile().name is None


def test_resume_path_validation_requires_existing_file(tmp_path):
    missing_profile = ApplicationProfile(resume_path=tmp_path / "missing.pdf")
    with pytest.raises(ValueError, match="not a file"):
        missing_profile.validate_resume_file()

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"test fixture")
    profile = ApplicationProfile(resume_path=resume)

    assert profile.validate_resume_file() == resume


def test_mapper_fills_safe_fields_and_explicit_consequential_answers():
    profile = ApplicationProfile(
        name="Test Person",
        email="person@example.com",
        work_authorization="Authorized to work",
        requires_sponsorship=False,
        education=[EducationEntry(degree="BSc", institution="Example University")],
    )
    form = make_form(
        ApplicationQuestion(key="name", label="Full Name"),
        ApplicationQuestion(key="email", label="Email Address"),
        ApplicationQuestion(key="authorization", label="Work Authorization"),
        ApplicationQuestion(key="sponsorship", label="Require Sponsorship"),
        ApplicationQuestion(key="school", label="Institution"),
    )

    payload = map_application_questions(
        form,
        profile,
        application_id="application-id",
        job=make_job(),
    )

    assert payload.fields == {
        "name": "Test Person",
        "email": "person@example.com",
        "authorization": "Authorized to work",
        "sponsorship": "No",
        "school": "Example University",
    }
    assert not payload.needs_review


def test_mapper_uses_configured_answers_only_for_known_consequential_fields():
    profile = ApplicationProfile(
        configured_answers={
            "Willing to relocate?": "Yes",
            "Why should we hire you?": "I am a great fit",
        }
    )
    form = make_form(
        ApplicationQuestion(key="relocation", label="Willing to Relocate?"),
        ApplicationQuestion(key="why", label="Why should we hire you?"),
    )

    payload = map_application_questions(
        form,
        profile,
        application_id="application-id",
        job=make_job(),
    )

    assert payload.fields == {"relocation": "Yes"}
    assert [question.key for question in payload.review_questions] == ["why"]


@pytest.mark.parametrize("email", ["bad", "no-domain@example", "has space@example.com"])
def test_application_profile_rejects_invalid_email(email):
    with pytest.raises(ValueError, match="valid email"):
        ApplicationProfile(email=email)


def test_application_profile_normalizes_email():
    assert ApplicationProfile(email=" Person@Example.com ").email == "person@example.com"


def test_mapper_sends_unknown_and_missing_fields_to_review():
    form = make_form(
        ApplicationQuestion(key="email", label="Email"),
        ApplicationQuestion(key="motivation", label="Why do you want this job?"),
    )

    payload = map_application_questions(
        form,
        ApplicationProfile(),
        application_id="application-id",
        job=make_job(),
    )

    assert payload.review_questions[0].key == "motivation"
    assert payload.missing_profile_questions[0].key == "email"
    assert payload.needs_review


def test_resume_is_mapped_only_when_form_requests_file(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"test fixture")
    profile = ApplicationProfile(resume_path=resume)

    payload = map_application_questions(
        make_form(
            ApplicationQuestion(
                key="resume_file",
                label="Resume",
                question_type=QuestionType.file,
            )
        ),
        profile,
        application_id="application-id",
        job=make_job(),
    )

    assert payload.fields["resume_file"] == str(resume)


def test_unsupported_platform_is_persisted_to_recovery_queue(tmp_path):
    tracker, attempts, application, engine = make_engine(tmp_path)

    result = engine.process(application, make_job(), ApplicationProfile())

    assert result.status == ApplicationResultStatus.unsupported
    assert tracker.get(application.job_url).automation_status == AutomationStatus.unsupported
    assert RecoveryQueue(attempts).list()[0].failure_category == FailureCategory.unsupported_platform


def test_unknown_question_prevents_submission_and_requires_review(tmp_path):
    adapter = FakeAdapter(
        form=make_form(
            ApplicationQuestion(key="name", label="Full Name"),
            ApplicationQuestion(key="why", label="Why should we hire you?"),
        )
    )
    tracker, _, application, engine = make_engine(tmp_path, adapter)

    result = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
    )

    assert result.status == ApplicationResultStatus.needs_review
    assert adapter.submit_calls == 0
    assert adapter.fill_calls == 0
    assert result.attempt.review_questions[0].label == "Why should we hire you?"
    assert tracker.get(application.job_url).status == ApplicationStatus.discovered


def test_human_review_answer_can_continue_only_with_explicit_approval(tmp_path):
    adapter = FakeAdapter(
        form=make_form(
            ApplicationQuestion(key="name", label="Full Name"),
            ApplicationQuestion(key="why", label="Why should we hire you?"),
        )
    )
    _, _, application, engine = make_engine(tmp_path, adapter)
    profile = ApplicationProfile(name="Test Person")
    answers = {"why": "A user-authored response"}

    result = engine.process(
        application,
        make_job(),
        profile,
        review_answers=answers,
    )
    assert result.status == ApplicationResultStatus.needs_review
    assert result.attempt.review_questions[0].label == "Why should we hire you?"

    continued = engine.process(
        application,
        make_job(),
        profile,
        review_answers=answers,
        approval=make_approval(adapter, make_job(), profile, review_answers=answers),
    )

    assert continued.status == ApplicationResultStatus.submitted
    assert continued.payload.fields["why"] == "A user-authored response"


def test_submission_requires_explicit_review_approval(tmp_path):
    adapter = FakeAdapter()
    _, _, application, engine = make_engine(tmp_path, adapter)

    result = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
    )

    assert result.status == ApplicationResultStatus.needs_review
    assert adapter.submit_calls == 0
    assert adapter.fill_calls == 0


def test_human_action_boundary_stops_before_fill_or_submit(tmp_path):
    adapter = FakeAdapter(
        form=ApplicationForm(
            platform="test_platform",
            application_url="https://example.com/jobs/android/apply",
            human_action_required=True,
            human_action_reason="CAPTCHA present",
        )
    )
    tracker, attempts, application, engine = make_engine(tmp_path, adapter)

    result = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )

    assert result.status == ApplicationResultStatus.human_action_required
    assert adapter.fill_calls == 0
    assert adapter.submit_calls == 0
    assert tracker.get(application.job_url).automation_status == AutomationStatus.human_action_required
    assert RecoveryQueue(attempts).list()[0].outcome == AttemptOutcome.human_action_required


def test_human_action_after_submit_call_blocks_automatic_retry(tmp_path):
    adapter = FakeAdapter(
        receipt=SubmissionReceipt(
            submission_outcome=SubmissionOutcome.unknown,
            human_action_required=True,
            error_message="MFA is required to confirm the result",
        )
    )
    tracker, _, application, engine = make_engine(tmp_path, adapter)

    result = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )
    repeated = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
        retry=True,
    )

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert repeated.status == ApplicationResultStatus.duplicate_submission_blocked
    assert tracker.get(application.job_url).automation_status == AutomationStatus.unknown_submission_result
    assert adapter.submit_calls == 1


def test_verified_submission_marks_applied_and_blocks_duplicate(tmp_path):
    adapter = FakeAdapter()
    tracker, attempts, application, engine = make_engine(tmp_path, adapter)
    profile = ApplicationProfile(name="Test Person")

    result = engine.process(
        application,
        make_job(),
        profile,
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )
    repeated = engine.process(
        application,
        make_job(),
        profile,
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
        retry=True,
    )

    saved_application = tracker.get(application.job_url)
    assert result.status == ApplicationResultStatus.submitted
    assert result.attempt.verification_result is True
    assert saved_application.status == ApplicationStatus.applied
    assert saved_application.automation_status == AutomationStatus.submitted
    assert saved_application.submission_evidence == "Confirmation reference: test-only"
    assert repeated.status == ApplicationResultStatus.duplicate_submission_blocked
    assert adapter.submit_calls == 1
    assert len(attempts.list()) == 1


def test_retryable_pre_submit_failure_requires_explicit_retry(tmp_path):
    adapter = FakeAdapter(
        error=AdapterError(
            "temporary connection failure", retryable=True, submission_started=False
        )
    )
    _, attempts, application, engine = make_engine(tmp_path, adapter)
    profile = ApplicationProfile(name="Test Person")

    failed = engine.process(
        application,
        make_job(),
        profile,
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )
    assert failed.status == ApplicationResultStatus.failed
    assert failed.attempt.retryable is True
    assert failed.attempt.submission_outcome == SubmissionOutcome.not_started
    without_retry = engine.process(
        application,
        make_job(),
        profile,
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )
    adapter.error = None
    retried = engine.process(
        application,
        make_job(),
        profile,
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
        retry=True,
    )

    assert failed.status == ApplicationResultStatus.failed
    assert failed.attempt.retryable
    assert failed.attempt.submission_outcome == SubmissionOutcome.not_started
    assert without_retry.status == ApplicationResultStatus.retry_not_allowed
    assert retried.status == ApplicationResultStatus.submitted
    assert [item.attempt_number for item in attempts.list()] == [1, 2]


def test_nonretryable_failure_cannot_be_retried(tmp_path):
    adapter = FakeAdapter(error=AdapterError("invalid form", submission_started=False))
    _, _, application, engine = make_engine(tmp_path, adapter)

    failed = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )
    retried = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
        retry=True,
    )

    assert failed.status == ApplicationResultStatus.failed
    assert retried.status == ApplicationResultStatus.retry_not_allowed


def test_adapter_error_before_submission_boundary_is_retryable_when_classified(tmp_path):
    class FillFailureAdapter(FakeAdapter):
        fail_fill = True

        def fill(self, payload):
            if self.fail_fill:
                raise AdapterError(
                    "local fill failed before submission",
                    retryable=True,
                    submission_started=False,
                )
            return FillResult()

    adapter = FillFailureAdapter()
    _, _, application, engine = make_engine(tmp_path, adapter)
    profile = ApplicationProfile(name="Test Person")
    approval = make_approval(adapter, make_job(), profile)
    failed = engine.process(application, make_job(), profile, approval=approval)

    assert failed.status == ApplicationResultStatus.failed
    assert failed.attempt.retryable is True
    assert failed.attempt.submission_outcome == SubmissionOutcome.not_started
    adapter.fail_fill = False
    retried = engine.process(
        application, make_job(), profile, approval=approval, retry=True
    )
    assert retried.status == ApplicationResultStatus.submitted


def test_unknown_submission_result_is_not_retried(tmp_path):
    adapter = FakeAdapter(
        error=AdapterError("connection lost after submit", submission_started=True)
    )
    _, attempts, application, engine = make_engine(tmp_path, adapter)

    result = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )
    repeated = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
        retry=True,
    )

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert not result.attempt.retryable
    assert repeated.status == ApplicationResultStatus.duplicate_submission_blocked
    assert len(attempts.list()) == 1


def test_default_adapter_error_after_submit_boundary_is_unknown_and_not_retryable(tmp_path):
    adapter = FakeAdapter(error=AdapterError("ambiguous late failure"))
    tracker, attempts, application, engine = make_engine(tmp_path, adapter)
    result = engine.process(
        application, make_job(), ApplicationProfile(name="Test Person"), approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person"))
    )
    retry = engine.process(
        application, make_job(), ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")), retry=True,
    )

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.attempt.retryable is False
    assert result.attempt.submission_outcome == SubmissionOutcome.unknown
    assert result.attempt in RecoveryQueue(attempts).list()
    assert result.attempt not in RecoveryQueue(attempts).retryable()
    assert retry.status == ApplicationResultStatus.duplicate_submission_blocked
    assert len(attempts.list()) == 1
    assert tracker.get(application.job_url).automation_status == AutomationStatus.unknown_submission_result


def test_review_approval_is_bound_to_payload_job_and_unresolved_questions():
    job = make_job()
    payload = map_application_questions(
        ApplicationForm(
            platform="test_platform",
            application_url="https://example.com/apply",
            questions=[ApplicationQuestion(key="message", label="Message", required=False)],
        ),
        ApplicationProfile(name="Test Person"),
        application_id=application_id_for(job.url),
        job=job,
    )
    approval = ReviewApproval.approve(
        payload, job_url=job.url, acknowledged_question_keys={"message"}
    )
    assert approval.validates(payload, job.url)
    assert not ReviewApproval.approve(payload, job_url=job.url).validates(payload, job.url)
    assert not approval.model_copy(update={"application_id": "different-app"}).validates(payload, job.url)
    assert not approval.model_copy(update={"job_url": "https://example.com/jobs/other"}).validates(payload, job.url)
    assert not approval.model_copy(
        update={"acknowledged_question_keys": frozenset()}
    ).validates(payload, job.url)
    changed = payload.model_copy(update={"fields": {"message": "changed"}})
    assert not approval.validates(changed, job.url)
    assert not approval.validates(payload, "https://example.com/jobs/other")
    changed_question = ApplicationQuestion(key="custom", label="Custom question", required=False)
    added_question = payload.model_copy(
        update={
            "review_questions": [*payload.review_questions, changed_question],
            "unanswered_review_questions": [
                *payload.unanswered_review_questions,
                changed_question,
            ],
        }
    )
    removed_question = payload.model_copy(
        update={"review_questions": [], "unanswered_review_questions": []}
    )
    assert not approval.validates(added_question, job.url)
    assert not approval.validates(removed_question, job.url)
    other_job = make_job().model_copy(update={"url": "https://example.com/jobs/other"})
    other_payload = map_application_questions(
        make_form(ApplicationQuestion(key="message", label="Message", required=False)),
        ApplicationProfile(name="Test Person"),
        application_id=application_id_for(other_job.url),
        job=other_job,
    )
    assert not approval.validates(other_payload, other_job.url)


def test_unknown_submission_requires_human_reconciliation_before_retry(tmp_path):
    adapter = FakeAdapter(
        error=AdapterError("connection lost after submit", submission_started=True)
    )
    tracker, attempts, application, engine = make_engine(tmp_path, adapter)

    result = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )
    with pytest.raises(ValueError, match="evidence"):
        engine.resolve_unknown_submission(
            application.job_url,
            submitted=False,
            evidence=" ",
        )

    resolved = engine.resolve_unknown_submission(
        application.job_url,
        submitted=False,
        evidence="Checked applicant account; no application exists",
    )
    adapter.error = None
    retried = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
        retry=True,
    )

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert resolved.status == ApplicationResultStatus.failed
    assert resolved.attempt.retryable
    assert retried.status == ApplicationResultStatus.submitted
    assert [attempt.attempt_number for attempt in attempts.list()] == [1, 2]


def test_human_reconciliation_can_confirm_unknown_submission(tmp_path):
    adapter = FakeAdapter(
        error=AdapterError("connection lost after submit", submission_started=True)
    )
    tracker, _, application, engine = make_engine(tmp_path, adapter)
    engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )

    result = engine.resolve_unknown_submission(
        application.job_url,
        submitted=True,
        evidence="Confirmed in employer account",
    )

    assert result.status == ApplicationResultStatus.submitted
    assert tracker.get(application.job_url).status == ApplicationStatus.applied
    assert tracker.get(application.job_url).automation_status == AutomationStatus.submitted


def test_unverified_submission_does_not_mark_application_applied(tmp_path):
    adapter = FakeAdapter(
        verification=SubmissionVerification(
            outcome=VerificationOutcome.unknown,
            evidence="No supported confirmation evidence found",
        )
    )
    tracker, _, application, engine = make_engine(tmp_path, adapter)

    result = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert tracker.get(application.job_url).status == ApplicationStatus.discovered
    assert tracker.get(application.job_url).automation_status == AutomationStatus.unknown_submission_result


def test_verified_without_evidence_is_not_marked_applied(tmp_path):
    adapter = FakeAdapter(
        verification=SubmissionVerification(outcome=VerificationOutcome.verified)
    )
    tracker, _, application, engine = make_engine(tmp_path, adapter)

    result = engine.process(
        application,
        make_job(),
        ApplicationProfile(name="Test Person"),
        approval=make_approval(adapter, make_job(), ApplicationProfile(name="Test Person")),
    )

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert "without evidence bound" in result.message
    assert tracker.get(application.job_url).status == ApplicationStatus.discovered


def test_verified_claim_without_context_is_not_marked_applied(tmp_path):
    adapter = FakeAdapter(
        verification=SubmissionVerification(
            outcome=VerificationOutcome.verified,
            evidence="A confirmation phrase from an unrelated page",
        )
    )
    tracker, attempts, application, engine = make_engine(tmp_path, adapter)
    job = make_job()
    profile = ApplicationProfile(name="Test Person")
    result = engine.process(
        application,
        job,
        profile,
        approval=make_approval(adapter, job, profile),
    )

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert tracker.get(application.job_url).status == ApplicationStatus.discovered
    assert attempts.latest_for(application_id_for(application.job_url)).outcome == AttemptOutcome.unknown_submission_result


def test_attempt_persistence_round_trips_and_recovery_lists_records(tmp_path):
    store = AttemptStore(tmp_path / "attempts.json")
    attempt = store.start(
        application_id="app-id",
        company="Test Startup",
        job_title="Android Engineer",
        job_url="https://example.com/jobs/android",
        platform="test_platform",
    )
    attempt = store.update(
        attempt,
        completed_at=datetime.now(timezone.utc),
        outcome=AttemptOutcome.failed,
        failure_category=FailureCategory.submit_error,
        retryable=True,
        submission_outcome=SubmissionOutcome.not_submitted,
    )

    loaded = store.list()[0]

    assert loaded == attempt
    assert RecoveryQueue(store).retryable() == [attempt]


def test_persistent_submission_claim_blocks_recreation_and_unknown_retry(tmp_path):
    store_path = tmp_path / "attempts.json"
    store = AttemptStore(store_path)
    args = dict(
        application_id="claim-id",
        company="Test Startup",
        job_title="Android Engineer",
        job_url="https://example.com/jobs/android",
        platform="test_platform",
    )
    first = store.claim_submission(**args)
    assert first is not None
    assert store.claim_submission(**args) is None
    assert AttemptStore(store_path).claim_submission(**args) is None
    store.update(
        first,
        outcome=AttemptOutcome.unknown_submission_result,
        submission_outcome=SubmissionOutcome.unknown,
    )
    assert AttemptStore(store_path).claim_submission(**args) is None


def test_sequential_direct_claim_is_blocked_after_first_attempt(tmp_path):
    store = AttemptStore(tmp_path / "attempts.json")
    args = dict(
        application_id="sequential-id",
        company="Test Startup",
        job_title="Android Engineer",
        job_url="https://example.com/jobs/android",
        platform="test_platform",
    )
    first = store.claim_submission(**args)
    assert first is not None
    store.update(first, outcome=AttemptOutcome.failed)
    second = store.claim_submission(**args)
    assert second is None
    assert len(store.list()) == 1


def test_concurrent_persistent_claims_allow_only_one_winner(tmp_path):
    path = tmp_path / "concurrent-attempts.json"
    args = dict(
        application_id="concurrent-id",
        company="Test Startup",
        job_title="Android Engineer",
        job_url="https://example.com/jobs/android",
        platform="test_platform",
    )
    stores = [AttemptStore(path), AttemptStore(path)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda store: store.claim_submission(**args), stores))
    assert sum(result is not None for result in results) == 1
    assert len(AttemptStore(path).list()) == 1


def test_application_identifier_uses_existing_url_normalization():
    assert application_id_for("https://example.com/job#apply") == application_id_for(
        "https://example.com/job"
    )
