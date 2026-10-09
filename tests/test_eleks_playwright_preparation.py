from __future__ import annotations

from pathlib import Path

import pytest

from src.applications.adapters.browser import (
    BrowserAutomation,
    BrowserInteractionError,
    BrowserPageSnapshot,
    BrowserSubmissionOutcome,
    BrowserSubmissionResult,
    PlaywrightBrowser,
)
from src.applications.adapters.base import AdapterVerificationResult
from src.applications.adapters.eleks import ELEKS_HOST, EleksAdapter
from src.applications.adapters.verification import VisibleConfirmationTextVerifier
from src.applications.models import Application
from src.applications.profile import ApplicationProfile
from src.applications.submission import ApplicationResultStatus, VerificationOutcome, application_id_for
from src.applications.submission import ReviewApproval
from src.applications.recovery import AttemptStore


FIXTURE = Path(__file__).parent / "fixtures" / "browser_driver.html"
SUBMISSION_FIXTURE = Path(__file__).parent / "fixtures" / "eleks_submission.html"
NO_CONFIRMATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "eleks_submission_no_confirmation.html"
)
CHALLENGE_SUBMISSION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "eleks_submission_challenge.html"
)
ELEKS_URL = f"https://{ELEKS_HOST}/vacancies/local-test-role/"
CONFIRMATION_MARKER = "Local fixture confirmation: application received"


def make_application() -> Application:
    return Application(
        company="ELEKS",
        job_title="Local test role",
        job_url=ELEKS_URL,
    )


class LocalEleksBrowser:
    """Routes the ELEKS adapter's URL to a local fixture through the browser port."""

    def __init__(self, driver: PlaywrightBrowser, page_url: str = FIXTURE.resolve().as_uri()):
        self.driver = driver
        self.page_url = page_url
        self.requested_urls: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.uploaded: list[tuple[str, str]] = []
        self.checkboxes: list[tuple[str, bool]] = []
        self.clicked: list[str] = []
        self.submit_calls = 0
        self.attempt_store = None

    def open_url(self, url: str) -> None:
        self.requested_urls.append(url)
        self.driver.open_url(self.page_url)

    def inspect_page(self) -> BrowserPageSnapshot:
        return self.driver.inspect_page()

    def click_safe_control(self, selector: str) -> None:
        self.clicked.append(selector)
        self.driver.click_safe_control(selector)

    def locate_field(self, label: str) -> str | None:
        return self.driver.locate_field(label)

    def fill_field(self, key: str, value: str) -> None:
        self.filled.append((key, value))
        self.driver.fill_field(key, value)

    def set_checkbox(self, key: str, checked: bool) -> None:
        self.checkboxes.append((key, checked))
        self.driver.set_checkbox(key, checked)

    def upload_file(self, key: str, path: str) -> None:
        self.uploaded.append((key, path))
        self.driver.upload_file(key, path)

    def detect_human_action(self) -> str | None:
        return self.driver.detect_human_action()

    def submit_form(self, *, approved: bool) -> BrowserSubmissionResult:
        if approved:
            self.submit_calls += 1
        return self.driver.submit_form(approved=approved)

    def inspect_result(self) -> BrowserPageSnapshot:
        page = self.driver.inspect_result()
        if self.attempt_store is None:
            return page
        attempt = self.attempt_store.latest_for(application_id_for(ELEKS_URL))
        if attempt is None or CONFIRMATION_MARKER not in page.body_text:
            return page
        # Deterministic fixture-only context; this is not asserted to exist on ELEKS.
        return page.model_copy(update={
            "confirmation_application_id": attempt.application_id,
            "confirmation_job_url": attempt.job_url,
            "confirmation_attempt_number": attempt.attempt_number,
        })


@pytest.fixture
def driver():
    try:
        instance = PlaywrightBrowser()
    except BrowserInteractionError as exc:
        pytest.skip(str(exc))
    try:
        yield instance
    finally:
        instance.close()


def test_eleks_preparation_fills_only_safe_profile_fields(driver, tmp_path):
    resume = tmp_path / "candidate.pdf"
    resume.write_bytes(b"local resume fixture")
    browser: BrowserAutomation = LocalEleksBrowser(driver)
    adapter = EleksAdapter(browser)
    profile = ApplicationProfile(
        name="Example Candidate",
        email="candidate@example.test",
        phone="+1 555 0100",
        resume_path=resume,
    )

    result = adapter.prepare(make_application(), profile)

    assert result.status == ApplicationResultStatus.needs_review
    assert result.payload is not None
    assert any(question.label == "Message" for question in result.payload.unanswered_review_questions)
    assert browser.requested_urls == [ELEKS_URL]
    assert browser.filled == [
        ("full_name", "Example Candidate"),
        ("email", "candidate@example.test"),
        ("phone", "+1 555 0100"),
    ]
    assert browser.uploaded == [("resume", str(resume))]
    assert browser.checkboxes == [("updates", False)]
    assert browser.clicked == []
    assert browser.submit_calls == 0


def test_eleks_preparation_honors_explicit_vacancy_update_preference(driver):
    browser = LocalEleksBrowser(driver)
    adapter = EleksAdapter(browser)

    result = adapter.prepare(
        make_application(),
        ApplicationProfile(configured_answers={"eleks_vacancy_updates": "yes"}),
    )

    assert result.status == ApplicationResultStatus.needs_review
    assert browser.checkboxes == [("updates", True)]
    assert browser.submit_calls == 0


def test_eleks_security_challenge_stops_before_any_field_changes(driver, tmp_path):
    challenge_page = tmp_path / "challenge.html"
    challenge_page.write_text(
        "<html><title>Security check</title><body>CAPTCHA verification required</body></html>",
        encoding="utf-8",
    )
    browser = LocalEleksBrowser(driver, challenge_page.resolve().as_uri())

    result = EleksAdapter(browser).prepare(
        make_application(),
        ApplicationProfile(name="Example Candidate", email="candidate@example.test"),
    )

    assert result.status == ApplicationResultStatus.human_action_required
    assert browser.filled == []
    assert browser.uploaded == []
    assert browser.checkboxes == []
    assert browser.submit_calls == 0


def test_eleks_security_challenge_during_filling_stops_remaining_fields(driver, tmp_path):
    resume = tmp_path / "candidate.pdf"
    resume.write_bytes(b"local resume fixture")

    class LateChallengeBrowser(LocalEleksBrowser):
        challenge_detected = False

        def fill_field(self, key: str, value: str) -> None:
            super().fill_field(key, value)
            if key == "full_name":
                self.challenge_detected = True

        def detect_human_action(self) -> str | None:
            if self.challenge_detected:
                return "MFA challenge detected"
            return super().detect_human_action()

    browser = LateChallengeBrowser(driver)
    result = EleksAdapter(browser).prepare(
        make_application(),
        ApplicationProfile(
            name="Example Candidate",
            email="candidate@example.test",
            phone="+1 555 0100",
            resume_path=resume,
        ),
    )

    assert result.status == ApplicationResultStatus.human_action_required
    assert browser.filled == [("full_name", "Example Candidate")]
    assert browser.uploaded == []
    assert browser.checkboxes == []
    assert browser.submit_calls == 0


def test_eleks_browser_fill_failure_becomes_structured_failure(driver):
    class FailingBrowser(LocalEleksBrowser):
        def fill_field(self, key: str, value: str) -> None:
            raise BrowserInteractionError("local fixture fill failed")

    browser = FailingBrowser(driver)
    result = EleksAdapter(browser).prepare(
        make_application(),
        ApplicationProfile(name="Example Candidate", email="candidate@example.test"),
    )

    assert result.status == ApplicationResultStatus.failed
    assert "local fixture fill failed" in result.message
    assert browser.submit_calls == 0


def _prepare_submission_adapter(browser, tmp_path, *, verifier=None):
    resume = tmp_path / "candidate.pdf"
    resume.write_bytes(b"local resume fixture")
    attempt_store = AttemptStore(tmp_path / "attempts.json")
    browser.attempt_store = attempt_store
    adapter = EleksAdapter(
        browser,
        verifier=verifier,
        attempt_store=attempt_store,
    )
    result = adapter.prepare(
        make_application(),
        ApplicationProfile(
            name="Example Candidate",
            email="candidate@example.test",
            phone="+1 555 0100",
            resume_path=resume,
        ),
    )
    assert result.status == ApplicationResultStatus.needs_review
    return adapter


def _approval(adapter):
    payload = next(iter(adapter._preparation_results.values())).payload
    return ReviewApproval.approve(
        payload,
        job_url=ELEKS_URL,
        acknowledged_question_keys={question.key for question in payload.unanswered_review_questions},
    )


def test_submission_requires_explicit_approval_then_verifies_local_confirmation(
    driver, tmp_path
):
    browser = LocalEleksBrowser(driver, SUBMISSION_FIXTURE.resolve().as_uri())
    verifier = VisibleConfirmationTextVerifier(
        browser,
        confirmation_marker=CONFIRMATION_MARKER,
    )
    adapter = _prepare_submission_adapter(browser, tmp_path, verifier=verifier)

    blocked = adapter.submit(make_application())

    assert blocked.receipt.submission_outcome.value == "not_started"
    assert "approval" in blocked.message.casefold()
    assert browser.submit_calls == 0

    approval = _approval(adapter)
    submitted = adapter.submit(make_application(), approval=approval)

    assert submitted.status == ApplicationResultStatus.submitted
    assert submitted.receipt.submission_outcome.value == "submitted"
    assert adapter.verify_result(make_application()).verification.outcome == VerificationOutcome.verified
    assert browser.submit_calls == 1
    assert browser.driver.inspect_page().title == "Local Confirmation Fixture"
    assert adapter.submit(make_application(), approval=approval).status == ApplicationResultStatus.duplicate_submission_blocked
    assert browser.submit_calls == 1


def test_verified_without_evidence_is_unknown_and_does_not_complete_platform_attempt(
    driver, tmp_path
):
    class EvidenceFreeVerifier:
        def verify(self):
            from src.applications.submission import SubmissionVerification

            return AdapterVerificationResult(
                status=ApplicationResultStatus.submitted,
                verification=SubmissionVerification(
                    outcome=VerificationOutcome.verified,
                    evidence=None,
                ),
            )

    browser = LocalEleksBrowser(driver, SUBMISSION_FIXTURE.resolve().as_uri())
    adapter = _prepare_submission_adapter(
        browser, tmp_path, verifier=EvidenceFreeVerifier()
    )
    result = adapter.submit(make_application(), approval=_approval(adapter))

    assert result.status == ApplicationResultStatus.unknown_submission_result
    persisted = AttemptStore(tmp_path / "attempts.json").list()
    assert persisted[-1].outcome.value == "unknown_submission_result"
    assert persisted[-1].verification_result is not True


def test_page_change_without_explicit_confirmation_is_unknown_and_not_retried(
    driver, tmp_path
):
    browser = LocalEleksBrowser(driver, NO_CONFIRMATION_FIXTURE.resolve().as_uri())
    adapter = _prepare_submission_adapter(browser, tmp_path)

    approval = _approval(adapter)
    submitted = adapter.submit(make_application(), approval=approval)
    repeated = adapter.submit(make_application(), approval=approval)

    assert submitted.status == ApplicationResultStatus.unknown_submission_result
    assert submitted.receipt.submission_outcome.value == "unknown"
    assert not submitted.receipt.retryable
    assert repeated.status == ApplicationResultStatus.duplicate_submission_blocked
    assert browser.submit_calls == 1
    recreated = _prepare_submission_adapter(browser, tmp_path)
    after_reload = recreated.submit(make_application(), approval=_approval(recreated))
    assert after_reload.status == ApplicationResultStatus.duplicate_submission_blocked
    assert browser.submit_calls == 1


def test_eleks_duplicate_is_blocked_after_adapter_recreation_and_state_reload(driver, tmp_path):
    attempts_path = tmp_path / "durable-attempts.json"
    browser = LocalEleksBrowser(driver, SUBMISSION_FIXTURE.resolve().as_uri())
    verifier = VisibleConfirmationTextVerifier(
        browser, confirmation_marker=CONFIRMATION_MARKER
    )
    first = EleksAdapter(browser, verifier=verifier, attempt_store=AttemptStore(attempts_path))
    browser.attempt_store = first.attempt_store
    resume = tmp_path / "candidate.pdf"
    resume.write_bytes(b"local resume fixture")
    profile = ApplicationProfile(
        name="Example Candidate", email="candidate@example.test",
        phone="+1 555 0100", resume_path=resume,
    )
    first.prepare(make_application(), profile)
    submitted = first.submit(make_application(), approval=_approval(first))
    assert submitted.status == ApplicationResultStatus.submitted

    recreated = EleksAdapter(
        browser,
        verifier=verifier,
        attempt_store=AttemptStore(attempts_path),
    )
    recreated.prepare(make_application(), profile)
    blocked = recreated.submit(make_application(), approval=_approval(recreated))

    assert blocked.status == ApplicationResultStatus.duplicate_submission_blocked
    assert browser.submit_calls == 1


def test_ambiguous_browser_failure_after_click_remains_unknown(driver, tmp_path):
    class AmbiguousBrowser(LocalEleksBrowser):
        def submit_form(self, *, approved: bool) -> BrowserSubmissionResult:
            super().submit_form(approved=approved)
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.unknown,
                message="Browser timed out after dispatch.",
                action_started=True,
            )

    browser = AmbiguousBrowser(driver, SUBMISSION_FIXTURE.resolve().as_uri())
    adapter = _prepare_submission_adapter(browser, tmp_path)

    result = adapter.submit(make_application(), approval=_approval(adapter))

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.receipt.submission_outcome.value == "unknown"
    assert browser.submit_calls == 1


def test_security_challenge_before_submit_stops_without_browser_submit(driver, tmp_path):
    class ArmedChallengeBrowser(LocalEleksBrowser):
        challenge_armed = False

        def detect_human_action(self) -> str | None:
            if self.challenge_armed:
                return "CAPTCHA challenge detected"
            return super().detect_human_action()

    browser = ArmedChallengeBrowser(driver, SUBMISSION_FIXTURE.resolve().as_uri())
    adapter = _prepare_submission_adapter(browser, tmp_path)
    browser.challenge_armed = True

    result = adapter.submit(make_application(), approval=_approval(adapter))

    assert result.status == ApplicationResultStatus.human_action_required
    assert result.receipt.submission_outcome.value == "not_started"
    assert browser.submit_calls == 0


def test_security_challenge_after_submit_is_unknown(driver, tmp_path):
    browser = LocalEleksBrowser(
        driver,
        CHALLENGE_SUBMISSION_FIXTURE.resolve().as_uri(),
    )
    adapter = _prepare_submission_adapter(
        browser,
        tmp_path,
        verifier=VisibleConfirmationTextVerifier(
            browser,
            confirmation_marker=CONFIRMATION_MARKER,
        ),
    )

    result = adapter.submit(make_application(), approval=_approval(adapter))

    assert result.status == ApplicationResultStatus.unknown_submission_result
    assert result.receipt.submission_outcome.value == "unknown"
    assert browser.submit_calls == 1
