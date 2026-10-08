from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class BrowserField(BaseModel):
    key: str
    label: str
    kind: str = "text"
    required: bool = False
    accept: list[str] = Field(default_factory=list)
    max_file_size: int | None = None
    honeypot: bool = False


class BrowserControl(BaseModel):
    label: str
    selector: str
    safe_to_click: bool = False


class BrowserPageSnapshot(BaseModel):
    url: str
    title: str = ""
    body_text: str = ""
    form_found: bool = False
    form_action: str | None = None
    form_method: str | None = None
    form_enctype: str | None = None
    fields: list[BrowserField] = Field(default_factory=list)
    controls: list[BrowserControl] = Field(default_factory=list)
    # A driver must surface challenges as state, never attempt to solve them.
    human_action_required: str | None = None


class BrowserInteractionError(RuntimeError):
    """A browser operation failed before its effect could be established."""


class BrowserHumanActionRequired(BrowserInteractionError):
    """A detected security gate requires a person to act."""


class BrowserSubmissionOutcome(StrEnum):
    """Possible outcomes when a browser submit action has been explicitly invoked."""

    not_started = "not_started"
    submitted = "submitted"
    unknown = "unknown"


class BrowserSubmissionResult(BaseModel):
    outcome: str
    message: str | None = None
    action_started: bool = False
    human_action_required: bool = False


class BrowserAutomation(Protocol):
    def open_url(self, url: str) -> None: ...

    def inspect_page(self) -> BrowserPageSnapshot: ...

    def click_safe_control(self, selector: str) -> None: ...

    def locate_field(self, label: str) -> str | None: ...

    def fill_field(self, key: str, value: str) -> None: ...

    def set_checkbox(self, key: str, checked: bool) -> None: ...

    def upload_file(self, key: str, path: str) -> None: ...

    def detect_human_action(self) -> str | None: ...

    def submit_form(self, *, approved: bool) -> BrowserSubmissionResult: ...

    def inspect_result(self) -> BrowserPageSnapshot: ...


class PlaywrightBrowser:
    """Concrete browser port implementation. Requires Playwright and Chromium."""

    def __init__(self, *, headless: bool = True) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserInteractionError(
                "Playwright is not installed; install project requirements first."
            ) from exc

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=headless)
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
        except Exception as exc:
            self._playwright.stop()
            raise BrowserInteractionError(
                f"Playwright browser could not be launched: {exc}"
            ) from exc

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._playwright.stop()

    def __enter__(self) -> "PlaywrightBrowser":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open_url(self, url: str) -> None:
        self._guard("open_url")
        try:
            self._page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            raise BrowserInteractionError(f"Browser navigation failed: {exc}") from exc
        challenge = self.detect_human_action()
        if challenge:
            raise BrowserHumanActionRequired(challenge)

    def inspect_page(self) -> BrowserPageSnapshot:
        self._guard("inspect_page")
        try:
            challenge = self.detect_human_action()
            fields = self._page.locator("input, textarea, select").evaluate_all(
                """els => els.map((el, i) => {
                    const label = el.labels && el.labels.length
                      ? Array.from(el.labels).map(x => x.innerText).join(' ').trim()
                      : (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || '');
                    const key = el.name || el.id || `field_${i}`;
                    const accept = (el.getAttribute('accept') || '').split(',').map(x => x.trim()).filter(Boolean);
                    return {key, label, kind: el.type || el.tagName.toLowerCase(), required: !!el.required,
                      accept, max_file_size: null,
                      honeypot: !!(el.closest('[aria-hidden="true"]') || el.getAttribute('tabindex') === '-1')};
                })"""
            )
            controls = self._page.locator("button, input[type=submit], input[type=button], a").evaluate_all(
                """els => els.map((el, i) => ({
                    label: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim(),
                    selector: el.id ? `#${CSS.escape(el.id)}` : `${el.tagName.toLowerCase()}:nth-of-type(${i + 1})`,
                    safe_to_click: !el.matches('button[type=submit], input[type=submit]') &&
                      (el.tagName.toLowerCase() !== 'a' || !!el.getAttribute('href'))
                }))"""
            )
            form = self._page.locator("form").first
            form_found = self._page.locator("form").count() > 0
            form_action = form.get_attribute("action") if form_found else None
            form_method = form.get_attribute("method") if form_found else None
            form_enctype = form.get_attribute("enctype") if form_found else None
            return BrowserPageSnapshot(
                url=self._page.url,
                title=self._page.title(),
                body_text=self._page.locator("body").inner_text(),
                form_found=form_found,
                form_action=form_action,
                form_method=form_method,
                form_enctype=form_enctype,
                fields=fields,
                controls=controls,
                human_action_required=challenge,
            )
        except BrowserInteractionError:
            raise
        except Exception as exc:
            raise BrowserInteractionError(f"Browser page inspection failed: {exc}") from exc

    def click_safe_control(self, selector: str) -> None:
        self._guard("click_safe_control")
        try:
            control = self._page.locator(selector)
            if control.evaluate(
                "el => el.matches('button[type=submit], input[type=submit]')"
            ):
                raise BrowserInteractionError(
                    "Browser submit controls are disabled by the driver safety boundary."
                )
            control.click()
        except BrowserInteractionError:
            raise
        except Exception as exc:
            raise BrowserInteractionError(f"Browser click failed: {exc}") from exc
        challenge = self.detect_human_action()
        if challenge:
            raise BrowserHumanActionRequired(challenge)

    def locate_field(self, label: str) -> str | None:
        for field in self.inspect_page().fields:
            if field.label.casefold() == label.casefold():
                return field.key
        return None

    def fill_field(self, key: str, value: str) -> None:
        self._guard("fill_field")
        try:
            self._field_locator(key).fill(value)
        except Exception as exc:
            raise BrowserInteractionError(f"Browser field fill failed: {exc}") from exc

    def set_checkbox(self, key: str, checked: bool) -> None:
        self._guard("set_checkbox")
        try:
            self._field_locator(key).set_checked(checked)
        except Exception as exc:
            raise BrowserInteractionError(f"Browser checkbox update failed: {exc}") from exc

    def upload_file(self, key: str, path: str) -> None:
        self._guard("upload_file")
        try:
            self._field_locator(key).set_input_files(str(Path(path).resolve(strict=True)))
        except Exception as exc:
            raise BrowserInteractionError(f"Browser file upload failed: {exc}") from exc

    def detect_human_action(self) -> str | None:
        try:
            url = self._page.url.casefold()
            title = self._page.title().casefold()
            body = self._page.locator("body").inner_text(timeout=1500).casefold()
            checks = (
                ("captcha", "CAPTCHA/security challenge detected; human action is required."),
                ("recaptcha", "CAPTCHA/security challenge detected; human action is required."),
                ("hcaptcha", "CAPTCHA/security challenge detected; human action is required."),
                ("two-factor", "MFA challenge detected; human action is required."),
                ("two factor", "MFA challenge detected; human action is required."),
                ("verification code", "MFA challenge detected; human action is required."),
                ("sign in", "Authentication is required; human action is required."),
                ("log in", "Authentication is required; human action is required."),
                ("login", "Authentication is required; human action is required."),
                ("access denied", "Security gate detected; human action is required."),
                ("unusual traffic", "Security gate detected; human action is required."),
            )
            combined = f"{url} {title} {body}"
            for marker, message in checks:
                if marker in combined:
                    return message
            challenge_selector = self._page.locator(
                'iframe[src*="captcha" i], [class*="captcha" i], [id*="captcha" i], '
                '[name*="captcha" i], input[autocomplete="one-time-code"]'
            )
            if challenge_selector.count():
                return "Security or MFA challenge detected; human action is required."
            return None
        except Exception as exc:
            raise BrowserInteractionError(f"Browser challenge inspection failed: {exc}") from exc

    def submit_form(self, *, approved: bool) -> BrowserSubmissionResult:
        if approved is not True:
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.not_started,
                message="Explicit approval is required before browser submission.",
            )

        challenge = self.detect_human_action()
        if challenge:
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.not_started,
                message=challenge,
                human_action_required=True,
            )

        try:
            submitter = self._page.locator(
                'form button[type="submit"], form input[type="submit"], '
                'form button:not([type])'
            ).first
            if submitter.count() == 0:
                raise BrowserInteractionError(
                    "No submit control was found in the inspected form."
                )
        except BrowserInteractionError:
            raise
        except Exception as exc:
            raise BrowserInteractionError(
                f"Browser submit control inspection failed: {exc}"
            ) from exc

        # Once click begins the request may reach the server even if Playwright
        # times out or the page changes. Treat every such failure as ambiguous.
        try:
            submitter.click()
        except Exception as exc:
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.unknown,
                message=f"Submit action may have started but did not complete cleanly: {exc}",
                action_started=True,
            )

        try:
            challenge = self.detect_human_action()
        except Exception as exc:
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.unknown,
                message=f"Submit action completed but page state is ambiguous: {exc}",
                action_started=True,
            )
        if challenge:
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.unknown,
                message=challenge,
                action_started=True,
                human_action_required=True,
            )
        return BrowserSubmissionResult(
            outcome=BrowserSubmissionOutcome.submitted,
            message="Submit control was activated; confirmation evidence is still required.",
            action_started=True,
        )

    def inspect_result(self) -> BrowserPageSnapshot:
        return self.inspect_page()

    def _field_locator(self, key: str):
        locator = self._page.locator(
            f'input[name="{_escape_attribute(key)}"], textarea[name="{_escape_attribute(key)}"], '
            f'select[name="{_escape_attribute(key)}"], #{_escape_identifier(key)}'
        )
        if locator.count() == 0:
            raise BrowserInteractionError(f"Browser field not found: {key}")
        return locator.first

    def _guard(self, operation: str) -> None:
        challenge = self.detect_human_action()
        if challenge:
            raise BrowserHumanActionRequired(
                f"Cannot {operation}: {challenge}"
            )


def _escape_attribute(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_identifier(value: str) -> str:
    return "".join(
        char if char.isalnum() or char in "_-" else f"\\{char}"
        for char in value
    )


class InMemoryBrowser:
    """Deterministic browser fake for tests; it performs no I/O or network access."""

    def __init__(
        self,
        page: BrowserPageSnapshot,
        *,
        challenge: str | None = None,
        failures: dict[str, str] | None = None,
    ) -> None:
        self.page = page
        self.challenge = challenge
        self.failures = dict(failures or {})
        self.operations: list[tuple[str, tuple[str, ...]]] = []
        self.values: dict[str, str] = {}
        self.uploads: dict[str, str] = {}
        self.checkboxes: dict[str, bool] = {}
        self.submit_calls = 0

    def _record(self, operation: str, *args: str) -> None:
        self.operations.append((operation, args))
        if operation in self.failures:
            raise BrowserInteractionError(self.failures[operation])
        if self.challenge:
            raise BrowserInteractionError(
                f"Human action required: {self.challenge}"
            )

    def open_url(self, url: str) -> None:
        self._record("open_url", url)

    def inspect_page(self) -> BrowserPageSnapshot:
        self._record("inspect_page")
        if self.challenge and not self.page.human_action_required:
            return self.page.model_copy(
                update={"human_action_required": self.challenge}
            )
        return self.page.model_copy(deep=True)

    def click_safe_control(self, selector: str) -> None:
        self._record("click_safe_control", selector)

    def locate_field(self, label: str) -> str | None:
        self._record("locate_field", label)
        field = next(
            (field for field in self.page.fields if field.label.casefold() == label.casefold()),
            None,
        )
        return field.key if field is not None else None

    def fill_field(self, key: str, value: str) -> None:
        self._record("fill_field", key, value)
        self.values[key] = value

    def set_checkbox(self, key: str, checked: bool) -> None:
        self._record("set_checkbox", key, str(checked).lower())
        self.checkboxes[key] = checked

    def upload_file(self, key: str, path: str) -> None:
        self._record("upload_file", key, path)
        self.uploads[key] = path

    def detect_human_action(self) -> str | None:
        self.operations.append(("detect_human_action", ()))
        return self.challenge or self.page.human_action_required

    def submit_form(self, *, approved: bool) -> BrowserSubmissionResult:
        if approved is not True:
            self.operations.append(("submit_blocked", ()))
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.not_started,
                message="Explicit approval is required before browser submission.",
            )
        self.operations.append(("submit_form", ()))
        if self.challenge or self.page.human_action_required:
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.not_started,
                message=self.challenge or self.page.human_action_required,
                human_action_required=True,
            )
        self.submit_calls += 1
        if "submit_form" in self.failures:
            return BrowserSubmissionResult(
                outcome=BrowserSubmissionOutcome.unknown,
                message=self.failures["submit_form"],
                action_started=True,
            )
        return BrowserSubmissionResult(
            outcome=BrowserSubmissionOutcome.submitted,
            message="In-memory submit action recorded.",
            action_started=True,
        )

    def inspect_result(self) -> BrowserPageSnapshot:
        self._record("inspect_result")
        return self.inspect_page()
