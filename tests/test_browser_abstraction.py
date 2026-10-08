from __future__ import annotations

import pytest

from src.applications.adapters.browser import (
    BrowserAutomation,
    BrowserField,
    BrowserInteractionError,
    BrowserPageSnapshot,
    BrowserSubmissionOutcome,
    InMemoryBrowser,
)


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
    assert browser.submit_form(approved=False).outcome == BrowserSubmissionOutcome.not_started


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
