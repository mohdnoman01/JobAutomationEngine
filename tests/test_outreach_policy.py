import pytest

from src.email.outreach_policy import (
    ContactQualification,
    annotate_contact,
    classify_contact,
    qualify_contact,
    rank_eligible_contacts,
    select_best_contact,
    select_relevant_job,
)
from src.research.models import Contact, Job


def make_contact(email: str, role: str | None = None) -> Contact:
    return Contact(email=email, company="Test Startup", role=role)


@pytest.mark.parametrize(
    ("contact", "expected"),
    [
        (make_contact("recruiter@example.com"), ContactQualification.eligible),
        (make_contact("talent@example.com"), ContactQualification.eligible),
        (make_contact("people@example.com", "Human Resources"), ContactQualification.eligible),
        (make_contact("jobs@example.com", "Hiring Manager"), ContactQualification.eligible),
        (make_contact("jane.doe@example.com"), ContactQualification.uncertain),
        (make_contact("sales@example.com"), ContactQualification.rejected),
        (make_contact("press@example.com"), ContactQualification.rejected),
        (make_contact("support@example.com"), ContactQualification.rejected),
        (make_contact("privacy@example.com"), ContactQualification.rejected),
        (make_contact("abc@ingest.sentry.io"), ContactQualification.rejected),
        (make_contact("you@company.com"), ContactQualification.rejected),
        (make_contact("logo@2x.png"), ContactQualification.rejected),
    ],
)
def test_qualify_contact(contact, expected):
    assert qualify_contact(contact) == expected


def test_classify_contact_records_auditable_reason():
    contact = make_contact("recruiter@example.com", "Recruiter")

    qualification, reason = classify_contact(contact)
    annotated_contact = annotate_contact(contact)

    assert qualification == ContactQualification.eligible
    assert reason == "eligible_role:recruit"
    assert annotated_contact.qualification == qualification
    assert annotated_contact.qualification_reason == reason


def test_rank_and_select_best_eligible_contact():
    contacts = [
        make_contact("hiring@example.com", "Hiring Manager"),
        make_contact("talent@example.com", "Talent Partner"),
        make_contact("recruiting@example.com", "Recruiting"),
        make_contact("jane.doe@example.com"),
    ]

    assert rank_eligible_contacts(contacts) == [
        contacts[2],
        contacts[1],
        contacts[0],
    ]
    assert select_best_contact(contacts) == contacts[2]


def test_select_best_contact_returns_none_without_eligible_contact():
    contacts = [
        make_contact("jane.doe@example.com"),
        make_contact("press@example.com"),
    ]

    assert select_best_contact(contacts) is None


def test_select_relevant_job_prefers_android_then_keeps_input_order():
    jobs = [
        Job(title="Backend Engineer", company="Test Startup", url="https://example.com/backend"),
        Job(title="Android Engineer", company="Test Startup", url="https://example.com/android"),
        Job(title="Kotlin Engineer", company="Test Startup", url="https://example.com/kotlin"),
    ]

    assert select_relevant_job(jobs) == jobs[1]
    assert select_relevant_job([]) is None
