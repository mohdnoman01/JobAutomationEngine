from __future__ import annotations

import pytest

from src.applications.recovery import AttemptOutcome, AttemptStore
from src.applications.adapters.browser import (
    BrowserAutomation,
    BrowserField,
    BrowserInteractionError,
    BrowserPageSnapshot,
    BrowserSubmissionOutcome,
    BrowserSubmissionAuthorization,
    InMemoryBrowser,
)
from src.applications.submission import ApplicationPayload, ReviewApproval, application_id_for


def sample_page() -> BrowserPageSnapshot:
    return BrowserPageSnapshot(
        url="https://example.test/apply",
        title="Application",
        form_found=True,
        fields=[
            BrowserField(key="name", label="Full Name", required=True),
            BrowserField(key="resume", label="Resume", kind="file"),
        ],
    )


def test_port_supports_page_inspection_and_field_mapping():
    browser: BrowserAutomation = InMemoryBrowser(sample_page())

    browser.open_url("https://example.test/apply")
    snapshot = browser.inspect_page()

    assert snapshot.form_found
    assert browser.locate_field("Full Name") == "name"
    assert browser.locate_field("missing") is None


def test_fake_records_fill_upload_and_click_without_external_io():
    browser = InMemoryBrowser(sample_page())

    browser.fill_field("name", "Example Candidate")
    browser.upload_file("resume", "fixture.pdf")
    browser.click_safe_control("#review")

    assert browser.values == {"name": "Example Candidate"}
    assert browser.uploads == {"resume": "fixture.pdf"}
    assert browser.operations == [
        ("fill_field", ("name", "Example Candidate")),
        ("upload_file", ("resume", "fixture.pdf")),
        ("click_safe_control", ("#review",)),
    ]
    assert browser.submit_form().outcome == BrowserSubmissionOutcome.not_started


def test_browser_failures_are_structured_exceptions():
    browser = InMemoryBrowser(sample_page(), failures={"inspect_page": "driver failed"})

    with pytest.raises(BrowserInteractionError, match="driver failed"):
        browser.inspect_page()


def test_challenge_stops_interaction_and_is_reported_as_human_action():
    browser = InMemoryBrowser(sample_page(), challenge="CAPTCHA detected")

    assert browser.detect_human_action() == "CAPTCHA detected"
    with pytest.raises(BrowserInteractionError, match="Human action required"):
        browser.fill_field("name", "Example Candidate")
    assert browser.values == {}


def test_browser_submission_requires_matching_approval_and_live_persisted_claim(tmp_path):
    job_url = "https://example.test/job"
    application_id = application_id_for(job_url)
    payload = ApplicationPayload(
        application_id=application_id,
        company="Example",
        job_title="Engineer",
        job_url=job_url,
        application_url="https://example.test/apply",
        platform="fixture",
        fields={"name": "Candidate"},
    )
    approval = ReviewApproval.approve(payload, job_url=job_url)
    store = AttemptStore(tmp_path / "attempts.json")
    started = store.start(
        application_id=application_id,
        company="Example",
        job_title="Engineer",
        job_url=job_url,
        platform="fixture",
    )
    claim = store.claim_submission(
        application_id=application_id,
        company="Example",
        job_title="Engineer",
        job_url=job_url,
        platform="fixture",
        attempt=started,
        payload_fingerprint=payload.fingerprint(),
    )
    browser = InMemoryBrowser(sample_page())

    valid_authorization = BrowserSubmissionAuthorization(
        approval=approval, payload=payload, attempt=claim, attempt_store=store
    )
    assert valid_authorization.failure_reason() is None
    assert browser.submit_form(authorization=valid_authorization).action_started
    assert browser.submit_form(authorization=valid_authorization).outcome == BrowserSubmissionOutcome.not_started

    stale_payload = payload.model_copy(update={"fields": {"name": "Changed"}})
    stale_authorization = BrowserSubmissionAuthorization(
        approval=approval, payload=stale_payload, attempt=claim, attempt_store=store
    )
    assert stale_authorization.failure_reason() is not None
    assert browser.submit_form(authorization=stale_authorization).outcome == BrowserSubmissionOutcome.not_started

    store.update(
        claim,
        outcome=AttemptOutcome.submitted,
        verification_result=True,
        verification_evidence="fixture confirmation",
    )
    assert valid_authorization.failure_reason() is not None
    assert browser.submit_form(authorization=valid_authorization).outcome == BrowserSubmissionOutcome.not_started
    assert browser.submit_calls == 1


def test_persistent_browser_action_claim_is_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    job_url = "https://example.test/job"
    app_id = application_id_for(job_url)
    store_path = tmp_path / "attempts.json"
    store = AttemptStore(store_path)
    started = store.start(
        application_id=app_id,
        company="Example",
        job_title="Engineer",
        job_url=job_url,
        platform="fixture",
    )
    claim = store.claim_submission(
        application_id=app_id,
        company="Example",
        job_title="Engineer",
        job_url=job_url,
        platform="fixture",
        attempt=started,
        payload_fingerprint="fingerprint",
    )
    args = dict(
        application_id=app_id,
        job_url=job_url,
        attempt_number=claim.attempt_number,
        payload_fingerprint="fingerprint",
    )
    stores = [AttemptStore(store_path), AttemptStore(store_path)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(
            executor.map(lambda item: item.begin_browser_submission_action(**args), stores)
        )
    assert sum(claimed) == 1
