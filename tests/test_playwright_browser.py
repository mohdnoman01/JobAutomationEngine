from __future__ import annotations

from pathlib import Path

import pytest

from src.applications.adapters.browser import (
    BrowserHumanActionRequired,
    BrowserInteractionError,
    BrowserSubmissionOutcome,
    PlaywrightBrowser,
)


FIXTURE = Path(__file__).parent / "fixtures" / "browser_driver.html"


@pytest.fixture
def browser():
    try:
        instance = PlaywrightBrowser()
    except BrowserInteractionError as exc:
        pytest.skip(str(exc))
    try:
        yield instance
    finally:
        instance.close()


def test_playwright_driver_local_fixture_flow(browser, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"local test fixture")

    browser.open_url(FIXTURE.resolve().as_uri())
    page = browser.inspect_page()

    assert page.title == "Local Application Fixture"
    assert page.form_found
    assert {field.kind for field in page.fields} >= {"text", "email", "file", "checkbox"}
    assert any(control.label == "Submit application" and not control.safe_to_click for control in page.controls)
    assert browser.locate_field("Full Name") == "full_name"

    browser.fill_field("full_name", "Example Candidate")
    browser.fill_field("email", "candidate@example.test")
    browser.upload_file("resume", str(resume))
    browser.click_safe_control("#updates")
    browser.click_safe_control("#test-submit")

    result = browser.inspect_result()
    assert result.url.startswith("file:")
    assert "Local fixture received" in result.body_text
    assert browser.submit_form(approved=False).outcome == BrowserSubmissionOutcome.not_started

    with pytest.raises(BrowserInteractionError, match="submit controls are disabled"):
        browser.click_safe_control("#native-submit")


def test_playwright_driver_converts_selector_errors(browser):
    browser.open_url(FIXTURE.resolve().as_uri())

    with pytest.raises(BrowserInteractionError, match="Browser click failed"):
        browser.click_safe_control("[")


def test_playwright_driver_stops_at_security_challenge(browser, tmp_path):
    challenge = tmp_path / "challenge.html"
    challenge.write_text(
        "<html><title>Challenge</title><body>CAPTCHA verification required</body></html>",
        encoding="utf-8",
    )

    with pytest.raises(BrowserHumanActionRequired, match="CAPTCHA"):
        browser.open_url(challenge.resolve().as_uri())

    with pytest.raises(BrowserHumanActionRequired, match="CAPTCHA"):
        browser.fill_field("full_name", "must not be entered")
