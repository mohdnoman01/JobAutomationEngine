from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import pytest

from src.applications.adapters.browser import (
    BrowserInteractionError,
    BrowserPageSnapshot,
    InMemoryBrowser,
    PlaywrightBrowser,
)
from src.applications.adapters.verification import VisibleConfirmationTextVerifier
from src.applications.profile import ApplicationProfile
from src.applications.recovery import ApplicationAttempt, AttemptStore, SubmissionOutcome
from src.applications.submission import (
    ApplicationCapabilities,
    ApplicationEngine,
    ApplicationForm,
    ApplicationQuestion,
    ApplicationResultStatus,
    FillResult,
    QuestionType,
    SubmissionReceipt,
    SubmissionVerification,
    VerificationOutcome,
    ReviewApproval,
    map_application_questions,
    application_id_for,
)
from src.applications.tracker import ApplicationTracker
from src.research.models import Job


FIXTURES = Path(__file__).parent / "fixtures"
CONFIRMATION = "Local fixture confirmation: application received"
APP_ID = "fixture-application"
JOB_URL = "https://fixture.invalid/jobs/role"


def expected_attempt(*, number=1, application_id=APP_ID, job_url=JOB_URL):
    return ApplicationAttempt(
        application_id=application_id, company="Fixture company",
        job_title="Fixture role", job_url=job_url, platform="local_fixture",
        attempt_number=number, started_at=datetime.now(timezone.utc),
    )


def bind_page(page, attempt):
    return page.model_copy(update={
        "confirmation_application_id": attempt.application_id,
        "confirmation_job_url": attempt.job_url,
        "confirmation_attempt_number": attempt.attempt_number,
    })


def test_explicit_local_confirmation_evidence_verifies_with_structured_evidence():
    try:
        browser = PlaywrightBrowser()
    except BrowserInteractionError as exc:
        pytest.skip(str(exc))
    try:
        browser.open_url((FIXTURES / "verification_confirmation.html").resolve().as_uri())
        attempt = expected_attempt()
        browser._page.locator("main[role=status]").evaluate(
            "(el, c) => { el.setAttribute('data-submission-context', ''); "
            "el.setAttribute('data-application-id', c.id); el.setAttribute('data-job-url', c.url); "
            "el.setAttribute('data-attempt-number', String(c.number)); }",
            {"id": attempt.application_id, "url": attempt.job_url, "number": attempt.attempt_number},
        )
        result = VisibleConfirmationTextVerifier(
            browser,
            confirmation_marker=CONFIRMATION,
        ).verify(attempt=attempt)
    finally:
        browser.close()

    assert result.status == ApplicationResultStatus.submitted
    assert result.verification.outcome == VerificationOutcome.verified
    assert result.evidence is not None
    assert result.evidence.mechanism == "visible_confirmation_text"
    assert result.evidence.expected_marker == CONFIRMATION
    assert result.evidence.observed_marker == CONFIRMATION


def test_absent_confirmation_is_unknown_even_when_page_can_be_inspected():
    browser = InMemoryBrowser(
        BrowserPageSnapshot(
            url="file:///local/changed.html",
            title="Different page",
            body_text="The page changed, but it contains no confirmation.",
        )
    )

    result = VisibleConfirmationTextVerifier(
        browser,
        confirmation_marker=CONFIRMATION,
    ).verify(attempt=expected_attempt())

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.verification.outcome == VerificationOutcome.unknown
    assert result.evidence is None


def test_wrong_confirmation_text_is_unknown():
    browser = InMemoryBrowser(
        BrowserPageSnapshot(
            url="file:///local/wrong-confirmation.html",
            body_text="Your profile has been saved.",
        )
    )
    result = VisibleConfirmationTextVerifier(
        browser, confirmation_marker=CONFIRMATION
    ).verify(attempt=expected_attempt())
    assert result.verification.outcome == VerificationOutcome.unknown
    assert result.evidence is None


def test_matching_phrase_on_unrelated_page_is_unknown():
    unrelated_url = "file:///local/unrelated-page.html"
    browser = InMemoryBrowser(
        BrowserPageSnapshot(url=unrelated_url, body_text=CONFIRMATION)
    )
    result = VisibleConfirmationTextVerifier(
        browser, confirmation_marker=CONFIRMATION
    ).verify(attempt=expected_attempt())

    # Phrase-only evidence and evidence on an unrelated URL cannot verify.
    assert result.verification.outcome == VerificationOutcome.unknown
    assert result.evidence is None


@pytest.mark.parametrize(
    "page",
    [
        lambda attempt: bind_page(
            BrowserPageSnapshot(url="file:///local/confirmation.html", body_text=CONFIRMATION),
            expected_attempt(job_url="https://fixture.invalid/jobs/other"),
        ),
        lambda attempt: BrowserPageSnapshot(
            url="file:///local/confirmation.html", body_text=CONFIRMATION
        ),
        lambda attempt: bind_page(
            BrowserPageSnapshot(url="file:///local/confirmation.html", body_text=""),
            attempt,
        ),
        lambda attempt: bind_page(
            BrowserPageSnapshot(url="file:///local/confirmation.html", body_text=CONFIRMATION),
            expected_attempt(number=attempt.attempt_number - 1),
        ),
    ],
    ids=["different-job", "missing-context", "empty-evidence", "replayed-attempt"],
)
def test_context_mismatch_missing_evidence_and_replay_are_unknown(page):
    attempt = expected_attempt(number=2)
    browser = InMemoryBrowser(page(attempt))

    result = VisibleConfirmationTextVerifier(
        browser, confirmation_marker=CONFIRMATION
    ).verify(attempt=attempt)

    assert result.verification.outcome == VerificationOutcome.unknown
    assert result.evidence is None


def test_browser_failure_after_possible_submission_is_unknown():
    browser = InMemoryBrowser(
        BrowserPageSnapshot(url="file:///local/result.html"),
        failures={"inspect_result": "connection lost after action"},
    )

    result = VisibleConfirmationTextVerifier(
        browser,
        confirmation_marker=CONFIRMATION,
    ).verify(attempt=expected_attempt())

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.verification.outcome == VerificationOutcome.unknown
    assert "connection lost after action" in result.message
    assert browser.operations == [("inspect_result", ())]


def test_verifier_without_adapter_marker_never_claims_success():
    browser = InMemoryBrowser(
        BrowserPageSnapshot(url="file:///local/result.html", body_text=CONFIRMATION)
    )

    result = VisibleConfirmationTextVerifier(
        browser,
        confirmation_marker=None,
    ).verify(attempt=expected_attempt())

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.verification.outcome == VerificationOutcome.unknown
    assert browser.operations == []


def test_security_challenge_during_verification_is_unknown():
    browser = InMemoryBrowser(
        BrowserPageSnapshot(
            url="file:///local/result.html",
            body_text=CONFIRMATION,
            human_action_required="MFA challenge detected",
        )
    )

    result = VisibleConfirmationTextVerifier(
        browser,
        confirmation_marker=CONFIRMATION,
    ).verify(attempt=expected_attempt())

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.verification.outcome == VerificationOutcome.unknown


def test_explicit_verification_can_complete_existing_engine_lifecycle(tmp_path):
    class RecordingBrowserPort:
        def __init__(self, browser):
            self.browser = browser
            self.calls = []

        def inspect_result(self):
            self.calls.append("inspect_result")
            return self.browser.inspect_result()

        def detect_human_action(self):
            self.calls.append("detect_human_action")
            return self.browser.detect_human_action()

        def submit_form(self, *, approved):
            self.calls.append("submit_form")
            return self.browser.submit_form(approved=approved)

    class FixtureAdapter:
        platform = "local_fixture"

        def __init__(self, browser):
            self.verifier = VisibleConfirmationTextVerifier(
                browser,
                confirmation_marker=CONFIRMATION,
            )

        def can_handle(self, job):
            return job.source == self.platform

        def capabilities(self, job):
            return ApplicationCapabilities(
                fill_known_fields=True,
                submit=True,
                verify_submission=True,
            )

        def prepare(self, job, profile):
            return ApplicationForm(
                platform=self.platform,
                application_url=job.url,
                questions=[
                    ApplicationQuestion(
                        key="name",
                        label="Full Name",
                        question_type=QuestionType.text,
                    )
                ],
            )

        def fill(self, payload):
            return FillResult()

        def submit(self, payload, *, approval=None):
            # The fixture adapter returns a canned receipt; it performs no action.
            return SubmissionReceipt(submission_outcome=SubmissionOutcome.submitted)

        def verify_result(self, payload, receipt, *, attempt):
            self.verifier.browser.browser._page.locator("main[role=status]").evaluate(
                "(el, c) => { el.setAttribute('data-submission-context', ''); "
                "el.setAttribute('data-application-id', c.id); el.setAttribute('data-job-url', c.url); "
                "el.setAttribute('data-attempt-number', String(c.number)); }",
                {"id": attempt.application_id, "url": attempt.job_url, "number": attempt.attempt_number},
            )
            return self.verifier.verify(attempt=attempt).verification

    try:
        browser = PlaywrightBrowser()
    except BrowserInteractionError as exc:
        pytest.skip(str(exc))
    try:
        browser.open_url((FIXTURES / "verification_confirmation.html").resolve().as_uri())
        browser_port = RecordingBrowserPort(browser)
        adapter = FixtureAdapter(browser_port)
        tracker = ApplicationTracker(tmp_path / "applications.json")
        attempts = AttemptStore(tmp_path / "attempts.json")
        job = Job(
            title="Fixture role",
            company="Fixture company",
            url="https://fixture.invalid/jobs/role",
            source="local_fixture",
        )
        application = tracker.create(
            company=job.company,
            job_title=job.title,
            job_url=job.url,
        )
        engine = ApplicationEngine(tracker, attempts, adapters=[adapter])

        profile = ApplicationProfile(name="Example Candidate")
        form = adapter.prepare(job, profile)
        payload = map_application_questions(
            form,
            profile,
            application_id=application_id_for(job.url),
            job=job,
        )
        approval = ReviewApproval.approve(payload, job_url=job.url)
        result = engine.process(
            application,
            job,
            profile,
            approval=approval,
        )

        assert result.status == ApplicationResultStatus.submitted
        assert tracker.get(job.url).status.value == "applied"
        assert browser_port.calls == ["inspect_result", "detect_human_action"]
    finally:
        browser.close()
