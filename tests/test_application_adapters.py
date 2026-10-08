from __future__ import annotations

from pathlib import Path

from src.applications.adapters.browser import (
    BrowserControl,
    BrowserField,
    BrowserHumanActionRequired,
    BrowserPageSnapshot,
)
from src.applications.adapters.eleks import EleksAdapter
from src.applications.models import Application
from src.applications.profile import ApplicationProfile
from src.applications.submission import ApplicationResultStatus, VerificationOutcome
from src.research.models import Job

ELEKS_URL = "https://careers.eleks.com/vacancies/senior-full-stack-developer-netreact-2/"


def eleks_form_snapshot(*, message=True, honeypot=True):
    fields = [
        BrowserField(key="input_1", label="Full Name", required=True),
        BrowserField(key="input_2", label="Email", kind="email", required=True),
        BrowserField(key="input_3", label="Phone", kind="tel", required=True),
    ]
    if message:
        fields.append(
            BrowserField(
                key="input_5",
                label="Message",
                kind="textarea",
                required=False,
            )
        )
    fields.extend(
        [
            BrowserField(
                key="input_4.1",
                label="Get updated about new vacancies at ELEKS",
                kind="checkbox",
            ),
            BrowserField(
                key="input_6",
                label="Attach a CV",
                kind="file",
                required=True,
                accept=[".jpg", ".gif", ".png", ".pdf", ".doc", ".docx"],
                max_file_size=10 * 1024 * 1024,
            ),
        ]
    )
    if honeypot:
        fields.insert(
            0,
            BrowserField(
                key="input_14",
                label="Name",
                kind="text",
                honeypot=True,
            ),
        )
    return BrowserPageSnapshot(
        url=ELEKS_URL,
        title="Senior Full-Stack Developer (.NET+React)",
        form_found=True,
        form_action=ELEKS_URL,
        form_method="post",
        form_enctype="multipart/form-data",
        fields=fields,
    )


class FakeBrowser:
    def __init__(self, page=None, *, challenge=None):
        self.page = page or eleks_form_snapshot()
        self.challenge = challenge
        self.opened_urls = []
        self.clicked = []
        self.filled = []
        self.uploaded = []
        self.checkboxes = []
        self.submit_calls = 0

    def open_url(self, url):
        self.opened_urls.append(url)

    def inspect_page(self):
        return self.page

    def click_safe_control(self, selector):
        self.clicked.append(selector)

    def locate_field(self, label):
        return next(
            (field.key for field in self.page.fields if field.label == label),
            None,
        )

    def fill_field(self, key, value):
        self.filled.append((key, value))

    def upload_file(self, key, path):
        self.uploaded.append((key, path))

    def set_checkbox(self, key, checked):
        self.checkboxes.append((key, checked))

    def detect_human_action(self):
        return self.challenge

    def submit_form(self, *, approved):
        if approved:
            self.submit_calls += 1
        raise AssertionError("ELEKS adapter must never submit")

    def inspect_result(self):
        return self.page


def make_application(url=ELEKS_URL):
    return Application(
        company="ELEKS",
        job_title="Senior Full-Stack Developer (.NET+React)",
        job_url=url,
    )


def make_job(url=ELEKS_URL):
    return Job(
        title="Senior Full-Stack Developer (.NET+React)",
        company="ELEKS",
        url=url,
        source="generic",
    )


def test_eleks_adapter_detects_vacancy_and_resolves_inline_apply_form():
    page = BrowserPageSnapshot(
        url=ELEKS_URL,
        title="Vacancy",
        controls=[BrowserControl(label="Apply", selector="#apply", safe_to_click=True)],
    )
    browser = FakeBrowser(page)

    class ApplyPopupBrowser(FakeBrowser):
        def click_safe_control(self, selector):
            super().click_safe_control(selector)
            self.page = eleks_form_snapshot()

    browser = ApplyPopupBrowser(page)
    adapter = EleksAdapter(browser)

    assert adapter.can_handle(make_job())
    assert not adapter.can_handle(make_job("https://example.com/vacancy"))
    result = adapter.inspect(make_job())

    assert result.status == ApplicationResultStatus.supported
    assert result.application_url == ELEKS_URL
    assert result.form.application_url == ELEKS_URL
    assert browser.opened_urls == [ELEKS_URL]
    assert browser.clicked == ["#apply"]
    assert browser.submit_calls == 0


def test_eleks_adapter_extracts_required_optional_and_honeypot_fields():
    adapter = EleksAdapter(FakeBrowser())

    result = adapter.inspect(make_job())

    fields = {field.label: field for field in result.fields}
    assert fields["Full Name"].required
    assert fields["Email"].required
    assert fields["Phone"].required
    assert fields["Attach a CV"].required
    assert not fields["Message"].required
    assert fields["Get updated about new vacancies at ELEKS"].kind == "checkbox"
    assert fields["Name"].honeypot
    assert all(question.label != "Name" for question in result.form.questions)


def test_untrusted_or_missing_apply_control_is_unsupported():
    browser = FakeBrowser(
        BrowserPageSnapshot(
            url=ELEKS_URL,
            controls=[BrowserControl(label="Apply", selector="#apply", safe_to_click=False)],
        )
    )
    result = EleksAdapter(browser).inspect(make_job())

    assert result.status == ApplicationResultStatus.unsupported
    assert browser.clicked == []
    assert browser.submit_calls == 0


def test_security_challenge_stops_before_apply_interaction():
    browser = FakeBrowser(challenge="Human verification is required")
    result = EleksAdapter(browser).inspect(make_job())

    assert result.status == ApplicationResultStatus.human_action_required
    assert result.message == "Human verification is required"
    assert browser.clicked == []
    assert browser.filled == []
    assert browser.uploaded == []
    assert browser.checkboxes == []
    assert browser.submit_calls == 0


def test_security_challenge_during_browser_interaction_requires_human_action():
    page = BrowserPageSnapshot(
        url=ELEKS_URL,
        controls=[BrowserControl(label="Apply", selector="#apply", safe_to_click=True)],
    )

    class ChallengingBrowser(FakeBrowser):
        def click_safe_control(self, selector):
            raise BrowserHumanActionRequired("MFA challenge detected")

    result = EleksAdapter(ChallengingBrowser(page)).inspect(make_job())

    assert result.status == ApplicationResultStatus.human_action_required
    assert "MFA challenge" in result.message


def test_browser_exception_becomes_structured_inspection_failure():
    class FailingBrowser(FakeBrowser):
        def inspect_page(self):
            raise RuntimeError("fake browser inspection failed")

    result = EleksAdapter(FailingBrowser()).inspect(make_job())

    assert result.status == ApplicationResultStatus.failed
    assert result.platform == "eleks_gravity_forms"
    assert result.application_url == ELEKS_URL
    assert result.message == (
        "ELEKS form inspection failed: fake browser inspection failed"
    )


def test_prepare_maps_profile_fields_and_defaults_vacancy_updates_off(tmp_path):
    resume = tmp_path / "candidate.pdf"
    resume.write_bytes(b"test resume fixture")
    profile = ApplicationProfile(
        name="Example Candidate",
        email="candidate@example.test",
        phone="+1 555 0100",
        resume_path=resume,
    )
    browser = FakeBrowser()
    adapter = EleksAdapter(browser)

    result = adapter.prepare(make_application(), profile)

    assert result.status == ApplicationResultStatus.needs_review
    assert result.payload.fields["input_1"] == "Example Candidate"
    assert result.payload.fields["input_2"] == "candidate@example.test"
    assert result.payload.fields["input_3"] == "+1 555 0100"
    assert result.payload.fields["input_6"] == str(resume)
    assert result.payload.fields["input_4.1"] == "false"
    assert [question.label for question in result.payload.unanswered_review_questions] == [
        "Message"
    ]
    assert browser.filled == [
        ("input_1", "Example Candidate"),
        ("input_2", "candidate@example.test"),
        ("input_3", "+1 555 0100"),
    ]
    assert browser.uploaded == [("input_6", str(resume))]
    assert browser.checkboxes == [("input_4.1", False)]
    assert browser.submit_calls == 0


def test_explicit_vacancy_update_preference_is_respected():
    profile = ApplicationProfile(
        configured_answers={"eleks_vacancy_updates": "yes"}
    )
    result = EleksAdapter(FakeBrowser()).prepare(make_application(), profile)

    assert result.payload.fields["input_4.1"] == "true"


def test_missing_required_profile_data_is_reported_for_review():
    result = EleksAdapter(FakeBrowser()).prepare(
        make_application(),
        ApplicationProfile(),
    )

    assert result.status == ApplicationResultStatus.needs_review
    assert {question.label for question in result.payload.missing_profile_questions} == {
        "Full Name",
        "Email",
        "Phone",
        "Attach a CV",
    }


def test_unsupported_resume_type_is_not_uploaded_or_submitted(tmp_path):
    resume = tmp_path / "candidate.exe"
    resume.write_bytes(b"not a resume")
    browser = FakeBrowser()

    result = EleksAdapter(browser).prepare(
        make_application(),
        ApplicationProfile(resume_path=resume),
    )

    assert result.status == ApplicationResultStatus.needs_review
    assert "file type is not accepted" in result.message
    assert browser.uploaded == []
    assert browser.submit_calls == 0


def test_prepare_review_blocks_submit_and_verification_is_unknown(tmp_path):
    resume = tmp_path / "candidate.pdf"
    resume.write_bytes(b"test resume fixture")
    application = make_application()
    browser = FakeBrowser()
    adapter = EleksAdapter(browser)

    prepared = adapter.prepare(
        application,
        ApplicationProfile(
            name="Example Candidate",
            email="candidate@example.test",
            phone="+1 555 0100",
            resume_path=resume,
        ),
    )
    submitted = adapter.submit(application)
    verification = adapter.verify_result(application)

    assert prepared.status == ApplicationResultStatus.needs_review
    assert submitted.status == ApplicationResultStatus.needs_review
    assert submitted.receipt.submission_outcome.value == "not_started"
    assert "approval" in submitted.message.casefold()
    assert verification.status == ApplicationResultStatus.unknown_submission_result
    assert verification.verification.outcome == VerificationOutcome.unknown
    assert browser.submit_calls == 0
    assert browser.filled == [
        ("input_1", "Example Candidate"),
        ("input_2", "candidate@example.test"),
        ("input_3", "+1 555 0100"),
    ]
    assert browser.uploaded == [("input_6", str(resume))]
    assert browser.checkboxes == [("input_4.1", False)]


def test_ready_preparation_still_requires_explicit_submission_approval(tmp_path):
    resume = tmp_path / "candidate.pdf"
    resume.write_bytes(b"test resume fixture")
    adapter = EleksAdapter(FakeReadyPage())
    application = make_application()
    prepared = adapter.prepare(
        application,
        ApplicationProfile(
            name="Example Candidate",
            email="candidate@example.test",
            phone="+1 555 0100",
            resume_path=resume,
        ),
    )
    result = adapter.submit(application)

    assert prepared.status == ApplicationResultStatus.ready_to_submit
    assert result.status == ApplicationResultStatus.needs_review
    assert result.receipt.submission_outcome.value == "not_started"
    assert "approval" in result.message.casefold()
    assert adapter.browser.submit_calls == 0


class FakeReadyPage(FakeBrowser):
    def __init__(self):
        super().__init__(eleks_form_snapshot(message=False, honeypot=True))
