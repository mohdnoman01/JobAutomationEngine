from __future__ import annotations

from pathlib import Path

import pytest

from src.applications.adapters.browser import (
    BrowserInteractionError,
    BrowserField,
    BrowserPageSnapshot,
    BrowserSubmissionOutcome,
    BrowserSubmissionResult,
    InMemoryBrowser,
)
from src.applications.adapters.eleks import EleksAdapter
from src.applications.adapters.eleks_bridge import EleksEngineBridge
from src.applications.adapters.verification import VisibleConfirmationTextVerifier
from src.applications.models import ApplicationStatus, AutomationStatus
from src.applications.profile import ApplicationProfile
from src.applications.recovery import AttemptStore, RecoveryQueue
from src.applications.submission import (
    ApplicationEngine,
    ApplicationResultStatus,
    ReviewApproval,
    SubmissionVerification,
    VerificationOutcome,
    application_id_for,
)
from src.applications.tracker import ApplicationTracker
from src.research.models import Job


ELEKS_URL = "https://careers.eleks.com/vacancies/local-engine-test/"
FIXTURES = Path(__file__).parent / "fixtures"
FORM_FIXTURE = FIXTURES / "eleks_submission.html"
CONFIRMATION_FIXTURE = FIXTURES / "verification_confirmation.html"
CONFIRMATION = "Local fixture confirmation: application received"


class FixtureSubmitBrowser(InMemoryBrowser):
    def __init__(
        self,
        page,
        attempt_store,
        application_id,
        *,
        fail_after_start=False,
        challenge=None,
    ):
        super().__init__(page, challenge=challenge)
        self.attempt_store = attempt_store
        self.application_id = application_id
        self.fail_after_start = fail_after_start
        self.claim_was_active_at_submit = False
        self.form_fixture_text = FORM_FIXTURE.read_text(encoding="utf-8")
        self.confirmation_fixture_text = CONFIRMATION_FIXTURE.read_text(encoding="utf-8")

    def submit_form(self, *, approved: bool) -> BrowserSubmissionResult:
        self.operations.append(("submit_form", ()))
        if approved is not True:
            return BrowserSubmissionResult(outcome=BrowserSubmissionOutcome.not_started)
        self.submit_calls += 1
        self.claim_was_active_at_submit = self.attempt_store.has_submission_in_progress(
            self.application_id
        )
        assert self.claim_was_active_at_submit
        assert 'name="full_name"' in self.form_fixture_text
        if self.fail_after_start:
            raise BrowserInteractionError("Local fixture browser failed after dispatch")
        assert CONFIRMATION in self.confirmation_fixture_text
        self.page = self.page.model_copy(
            update={
                "url": CONFIRMATION_FIXTURE.resolve().as_uri(),
                "title": "Local Confirmation Fixture",
                "body_text": CONFIRMATION,
                "form_found": False,
            }
        )
        attempt = self.attempt_store.latest_for(self.application_id)
        if attempt is not None:
            self.page = self.page.model_copy(update={
                "confirmation_application_id": attempt.application_id,
                "confirmation_job_url": attempt.job_url,
                "confirmation_attempt_number": attempt.attempt_number,
            })
        return BrowserSubmissionResult(
            outcome=BrowserSubmissionOutcome.submitted,
            message="Local fixture submission action",
            action_started=True,
        )


def make_page(*, challenge=None, honeypot=False):
    fields = [
        BrowserField(key="full_name", label="Full Name", required=True),
        BrowserField(key="email", label="Email", kind="email", required=True),
        BrowserField(key="phone", label="Phone", kind="tel", required=True),
        BrowserField(key="resume", label="Attach a CV", kind="file", required=True),
        BrowserField(key="updates", label="Get updated about new vacancies at ELEKS", kind="checkbox"),
        BrowserField(key="message", label="Message", kind="textarea"),
    ]
    if honeypot:
        fields.append(
            BrowserField(
                key="gform_honeypot",
                label="Full Name",
                kind="text",
                honeypot=True,
            )
        )
    return BrowserPageSnapshot(
        url=FORM_FIXTURE.resolve().as_uri(),
        title="Local ELEKS Form Fixture",
        body_text=challenge or "Local deterministic fixture",
        form_found=True,
        form_action="#",
        form_method="post",
        form_enctype="multipart/form-data",
        fields=fields,
        human_action_required=challenge,
    )


def make_engine(tmp_path: Path, *, fail_after_start=False, challenge=None, honeypot=False):
    attempts = AttemptStore(tmp_path / "attempts.json")
    application_id = application_id_for(ELEKS_URL)
    browser = FixtureSubmitBrowser(
        make_page(challenge=challenge, honeypot=honeypot),
        attempts,
        application_id,
        fail_after_start=fail_after_start,
        challenge=challenge,
    )
    adapter = EleksAdapter(
        browser,
        verifier=VisibleConfirmationTextVerifier(
            browser, confirmation_marker=CONFIRMATION
        ),
        attempt_store=attempts,
    )
    bridge = EleksEngineBridge(adapter)
    tracker = ApplicationTracker(tmp_path / "applications.json")
    application = tracker.create("ELEKS", "Local fixture role", ELEKS_URL)
    engine = ApplicationEngine(tracker, attempts, adapters=[bridge])
    job = Job(title=application.job_title, company=application.company, url=ELEKS_URL)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"local fixture resume")
    profile = ApplicationProfile(
        name="Fixture Candidate",
        email="candidate@example.test",
        phone="+1 555 0100",
        resume_path=resume,
    )
    return browser, attempts, tracker, application, engine, job, profile


def test_engine_processes_eleks_through_bridge_with_bound_approval_and_evidence(tmp_path):
    browser, attempts, tracker, application, engine, job, profile = make_engine(tmp_path)
    review = engine.process(application, job, profile)
    assert review.status == ApplicationResultStatus.needs_review
    assert review.payload is not None
    assert any(q.label == "Message" for q in review.payload.unanswered_review_questions)
    assert browser.values["full_name"] == "Fixture Candidate"
    assert browser.values["email"] == "candidate@example.test"
    assert browser.values["phone"] == "+1 555 0100"
    assert browser.uploads["resume"] == str(profile.resume_path)
    assert browser.checkboxes["updates"] is False
    assert browser.values.get("message") is None

    approval = ReviewApproval.approve(
        review.payload,
        job_url=job.url,
        acknowledged_question_keys={"message"},
    )
    completed = engine.process(application, job, profile, approval=approval)

    assert completed.status == ApplicationResultStatus.submitted
    assert completed.attempt.verification_evidence
    assert tracker.get(job.url).status == ApplicationStatus.applied
    assert tracker.get(job.url).automation_status == AutomationStatus.submitted
    assert browser.submit_calls == 1
    assert browser.claim_was_active_at_submit
    assert len(attempts.list()) == 2  # review attempt plus claimed submission attempt
    assert len(list(Path(tmp_path).glob("attempts.json.lock"))) == 1


def test_engine_records_unknown_eleks_result_and_blocks_automatic_retry(tmp_path):
    browser, attempts, tracker, application, engine, job, profile = make_engine(
        tmp_path, fail_after_start=True
    )
    review = engine.process(application, job, profile)
    approval = ReviewApproval.approve(
        review.payload,
        job_url=job.url,
        acknowledged_question_keys={"message"},
    )
    result = engine.process(application, job, profile, approval=approval)
    retry = engine.process(application, job, profile, approval=approval, retry=True)

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert retry.status == ApplicationResultStatus.duplicate_submission_blocked
    assert tracker.get(job.url).status == ApplicationStatus.discovered
    assert tracker.get(job.url).automation_status == AutomationStatus.unknown_submission_result
    assert browser.submit_calls == 1
    assert browser.claim_was_active_at_submit
    assert attempts.latest_for(application_id_for(job.url)).outcome.value == "unknown_submission_result"
    unknown_attempt = attempts.latest_for(application_id_for(job.url))
    assert unknown_attempt.retryable is False
    assert RecoveryQueue(attempts).list()[-1].outcome.value == "unknown_submission_result"

    # Simulate process restart: reload both stores and use a fresh adapter/bridge.
    reloaded_attempts = AttemptStore(tmp_path / "attempts.json")
    reloaded_tracker = ApplicationTracker(tmp_path / "applications.json")
    restarted_browser = FixtureSubmitBrowser(
        make_page(),
        reloaded_attempts,
        application_id_for(job.url),
    )
    restarted_adapter = EleksAdapter(
        restarted_browser,
        verifier=VisibleConfirmationTextVerifier(
            restarted_browser, confirmation_marker=CONFIRMATION
        ),
        attempt_store=reloaded_attempts,
    )
    restarted_engine = ApplicationEngine(
        reloaded_tracker,
        reloaded_attempts,
        adapters=[EleksEngineBridge(restarted_adapter)],
    )
    after_restart = restarted_engine.process(
        application, job, profile, approval=approval, retry=True
    )
    assert after_restart.status == ApplicationResultStatus.duplicate_submission_blocked
    assert restarted_browser.submit_calls == 0

    reconciled = restarted_engine.resolve_unknown_submission(
        job.url,
        submitted=True,
        evidence="Human checked the local test account and confirmed receipt.",
    )
    assert reconciled.status == ApplicationResultStatus.submitted
    assert reloaded_tracker.get(job.url).status == ApplicationStatus.applied


def test_bridge_rejects_direct_submission_without_persisted_engine_claim(tmp_path):
    browser, _, _, application, engine, job, profile = make_engine(tmp_path)
    review = engine.process(application, job, profile)
    approval = ReviewApproval.approve(
        review.payload,
        job_url=job.url,
        acknowledged_question_keys={"message"},
    )
    bridge = engine.adapters[0]
    receipt = bridge.submit(review.payload, approval=approval)

    assert receipt.submission_outcome.value == "not_started"
    assert browser.submit_calls == 0


def test_bridge_rejects_approved_payload_that_differs_from_platform_preparation(tmp_path):
    browser, _, _, application, engine, job, profile = make_engine(tmp_path)
    review = engine.process(application, job, profile)
    changed_payload = review.payload.model_copy(
        update={
            "fields": {
                **review.payload.fields,
                "message": "A response that was not mapped into the ELEKS form",
            }
        }
    )
    approval = ReviewApproval.approve(
        changed_payload,
        job_url=job.url,
        acknowledged_question_keys={"message"},
    )

    receipt = engine.adapters[0].submit(changed_payload, approval=approval)

    assert receipt.submission_outcome.value == "not_started"
    assert "do not match" in receipt.error_message
    assert browser.submit_calls == 0


def test_changed_prepared_payload_invalidates_engine_approval(tmp_path):
    browser, _, tracker, application, engine, job, profile = make_engine(tmp_path)
    review = engine.process(application, job, profile)
    approval = ReviewApproval.approve(
        review.payload,
        job_url=job.url,
        acknowledged_question_keys={"message"},
    )
    changed_profile = profile.model_copy(update={"name": "Changed Candidate"})
    result = engine.process(application, job, changed_profile, approval=approval)

    assert result.status == ApplicationResultStatus.needs_review
    assert browser.submit_calls == 0
    assert tracker.get(job.url).status == ApplicationStatus.discovered


@pytest.mark.parametrize(
    ("fixture_name", "challenge"),
    [
        ("security_captcha.html", "CAPTCHA required"),
        ("security_mfa.html", "Enter the verification code from your authenticator"),
        ("security_login.html", "Password sign in required"),
        ("security_sso.html", "Single sign-on authentication required"),
        ("security_challenge.html", "Security verification challenge detected"),
        ("security_unusual.html", "This automated request needs human verification"),
    ],
)
def test_security_fixture_stops_engine_before_fill_claim_or_submit(
    tmp_path, fixture_name, challenge
):
    source = (FIXTURES / fixture_name).read_text(encoding="utf-8").casefold()
    assert any(word in source for word in challenge.casefold().split()[:1])
    browser, attempts, tracker, application, engine, job, profile = make_engine(
        tmp_path, challenge=challenge
    )

    result = engine.process(application, job, profile)

    assert result.status == ApplicationResultStatus.human_action_required
    assert browser.values == {}
    assert browser.uploads == {}
    assert browser.checkboxes == {}
    assert browser.submit_calls == 0
    assert not attempts.has_submission_in_progress(application_id_for(job.url))
    assert all(attempt.outcome.value != "submitting" for attempt in attempts.list())
    assert tracker.get(job.url).automation_status == AutomationStatus.human_action_required


def test_local_honeypot_field_is_never_filled_or_submitted(tmp_path):
    fixture = (FIXTURES / "honeypot_form.html").read_text(encoding="utf-8")
    assert "gform_validation_container" in fixture
    browser, attempts, _, application, engine, job, profile = make_engine(
        tmp_path, honeypot=True
    )

    result = engine.process(application, job, profile)

    assert result.status == ApplicationResultStatus.needs_review
    assert "gform_honeypot" not in browser.values
    assert browser.submit_calls == 0
    assert not attempts.has_submission_in_progress(application_id_for(job.url))
