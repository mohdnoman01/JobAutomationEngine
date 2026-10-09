from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.applications.adapters.browser import (
    BrowserSubmissionAuthorization,
    BrowserPageSnapshot,
    InMemoryBrowser,
)
from src.applications.evidence import BrowserObservation, EvidenceObserver
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
        attempt_number=number, payload_fingerprint="fixture-payload-fingerprint",
        started_at=datetime.now(timezone.utc),
    )


def with_fixture_observation(page, attempt, *, job_url=None, number=None):
    observation = BrowserObservation(
        observer=EvidenceObserver.test_fixture,
        observation_id="fixture-observation",
        observed_url=page.url,
        application_id=attempt.application_id,
        job_url=job_url or attempt.job_url,
        attempt_number=attempt.attempt_number if number is None else number,
        payload_fingerprint=attempt.payload_fingerprint,
        action_started=True,
        page_changed_after_action=True,
    )
    return page.model_copy(update={"observation": observation})


def fixture_verifier(browser, *, url):
    return VisibleConfirmationTextVerifier(
        browser,
        confirmation_marker=CONFIRMATION,
        trusted_confirmation_urls={url},
    )


def test_explicit_local_confirmation_evidence_verifies_with_structured_evidence():
    attempt = expected_attempt()
    url = (FIXTURES / "verification_confirmation.html").resolve().as_uri()
    page = with_fixture_observation(
        BrowserPageSnapshot(
            url=url,
            body_text=(FIXTURES / "verification_confirmation.html").read_text(encoding="utf-8"),
        ),
        attempt,
    )
    browser = InMemoryBrowser(page)
    result = fixture_verifier(browser, url=url).verify(attempt=attempt)

    assert result.status == ApplicationResultStatus.submitted
    assert result.verification.outcome == VerificationOutcome.verified
    assert result.evidence is not None
    assert result.evidence.mechanism == "visible_confirmation_text"
    assert result.evidence.expected_marker == CONFIRMATION
    assert result.evidence.observed_marker == CONFIRMATION


def test_absent_confirmation_is_unknown_even_when_page_can_be_inspected():
    url = "file:///local/changed.html"
    browser = InMemoryBrowser(
        BrowserPageSnapshot(
            url=url,
            title="Different page",
            body_text="The page changed, but it contains no confirmation.",
        )
    )

    result = fixture_verifier(browser, url=url).verify(attempt=expected_attempt())

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.verification.outcome == VerificationOutcome.unknown
    assert result.evidence is None


def test_wrong_confirmation_text_is_unknown():
    url = "file:///local/wrong-confirmation.html"
    browser = InMemoryBrowser(
        BrowserPageSnapshot(
            url=url,
            body_text="Your profile has been saved.",
        )
    )
    result = fixture_verifier(browser, url=url).verify(attempt=expected_attempt())
    assert result.verification.outcome == VerificationOutcome.unknown
    assert result.evidence is None


def test_matching_phrase_on_unrelated_page_is_unknown():
    unrelated_url = "file:///local/unrelated-page.html"
    attempt = expected_attempt()
    browser = InMemoryBrowser(
        with_fixture_observation(
            BrowserPageSnapshot(url=unrelated_url, body_text=CONFIRMATION), attempt
        )
    )
    result = fixture_verifier(
        browser, url="file:///local/trusted-confirmation.html"
    ).verify(attempt=attempt)

    # Phrase-only evidence and evidence on an unrelated URL cannot verify.
    assert result.verification.outcome == VerificationOutcome.unknown
    assert result.evidence is None


@pytest.mark.parametrize(
    "page",
    [
        lambda attempt: with_fixture_observation(
            BrowserPageSnapshot(url="file:///local/confirmation.html", body_text=CONFIRMATION),
            attempt,
            job_url="https://fixture.invalid/jobs/other",
        ),
        lambda attempt: BrowserPageSnapshot(
            url="file:///local/confirmation.html", body_text=CONFIRMATION
        ),
        lambda attempt: with_fixture_observation(
            BrowserPageSnapshot(url="file:///local/confirmation.html", body_text=""),
            attempt,
        ),
        lambda attempt: with_fixture_observation(
            BrowserPageSnapshot(url="file:///local/confirmation.html", body_text=CONFIRMATION),
            attempt,
            number=attempt.attempt_number - 1,
        ),
    ],
    ids=["different-job", "missing-context", "empty-evidence", "replayed-attempt"],
)
def test_context_mismatch_missing_evidence_and_replay_are_unknown(page):
    attempt = expected_attempt(number=2)
    browser = InMemoryBrowser(page(attempt))

    result = fixture_verifier(
        browser, url="file:///local/confirmation.html"
    ).verify(attempt=attempt)

    assert result.verification.outcome == VerificationOutcome.unknown
    assert result.evidence is None


def test_browser_failure_after_possible_submission_is_unknown():
    browser = InMemoryBrowser(
        BrowserPageSnapshot(url="file:///local/result.html"),
        failures={"inspect_result": "connection lost after action"},
    )

    result = fixture_verifier(
        browser, url="file:///local/result.html"
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
        trusted_confirmation_urls={"file:///local/result.html"},
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

    result = fixture_verifier(
        browser, url="file:///local/result.html"
    ).verify(attempt=expected_attempt())

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.verification.outcome == VerificationOutcome.unknown


def test_explicit_verification_can_complete_existing_engine_lifecycle(tmp_path):
    class FixtureAdapter:
        platform = "local_fixture"

        def __init__(self, browser, attempts, confirmation_url):
            self.browser = browser
            self.attempts = attempts
            self.verifier = VisibleConfirmationTextVerifier(
                browser,
                confirmation_marker=CONFIRMATION,
                trusted_confirmation_urls={confirmation_url},
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
            attempt = self.attempts.latest_for(payload.application_id)
            result = self.browser.submit_form(
                authorization=BrowserSubmissionAuthorization(
                    approval=approval,
                    payload=payload,
                    attempt=attempt,
                    attempt_store=self.attempts,
                )
            )
            return SubmissionReceipt(
                submission_outcome=(
                    SubmissionOutcome.submitted
                    if result.action_started and result.outcome == "submitted"
                    else SubmissionOutcome.unknown
                    if result.action_started
                    else SubmissionOutcome.not_started
                ),
                error_message=result.message,
                human_action_required=result.human_action_required,
            )

        def verify_result(self, payload, receipt, *, attempt):
            return self.verifier.verify(attempt=attempt).verification

    confirmation_url = "file:///local/confirmation.html"
    result_page = BrowserPageSnapshot(
        url=confirmation_url,
        title="Local fixture confirmation",
        body_text=CONFIRMATION,
    )
    browser = InMemoryBrowser(
        BrowserPageSnapshot(url="file:///local/form.html", body_text="Local fixture form"),
        result_page=result_page,
    )
    tracker = ApplicationTracker(tmp_path / "applications.json")
    attempts = AttemptStore(tmp_path / "attempts.json")
    adapter = FixtureAdapter(browser, attempts, confirmation_url)
    job = Job(
        title="Fixture role",
        company="Fixture company",
        url=JOB_URL,
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
    result = engine.process(application, job, profile, approval=approval)

    assert result.status == ApplicationResultStatus.submitted
    assert tracker.get(job.url).status.value == "applied"
    assert browser.submit_calls == 1
    assert result.attempt.verification_evidence
