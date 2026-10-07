import pytest

from src.email.outreach_policy import (
    ContactQualification,
    annotate_contact,
    classify_contact,
    qualify_contact,
    rank_eligible_contacts,
    select_best_contact,
    select_relevant_job,
    select_relevant_jobs,
)
from src.research.models import Contact, Job, UserProfile


def make_contact(email: str, role: str | None = None) -> Contact:
    return Contact(email=email, company="Test Startup", role=role)


@pytest.mark.parametrize(
    ("contact", "expected"),
    [
        (make_contact("recruiter@example.com"), ContactQualification.eligible),
        (make_contact("talent@example.com"), ContactQualification.eligible),
        (
            make_contact("people@example.com", "Human Resources"),
            ContactQualification.eligible,
        ),
        (
            make_contact("jobs@example.com", "Hiring Manager"),
            ContactQualification.eligible,
        ),
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
        Job(
            title="Backend Engineer",
            company="Test Startup",
            url="https://example.com/backend",
        ),
        Job(
            title="Android Engineer",
            company="Test Startup",
            url="https://example.com/android",
        ),
        Job(
            title="Kotlin Engineer",
            company="Test Startup",
            url="https://example.com/kotlin",
        ),
    ]

    profile = UserProfile(
        target_roles=["Android Engineer"],
        preferred_skills=["Kotlin", "Jetpack Compose"],
    )
    assert select_relevant_job(jobs, profile) == jobs[1]
    assert select_relevant_job([], profile) is None


def test_select_relevant_job_prefers_android_title():
    jobs = [
        Job(
            title="Software Engineer",
            company="Test Startup",
            url="https://example.com/software",
            description="Works with Android teams.",
        ),
        Job(
            title="Android Developer",
            company="Test Startup",
            url="https://example.com/android",
        ),
    ]

    profile = UserProfile(
        target_roles=["Android Developer"],
        preferred_skills=["Kotlin", "Jetpack Compose"],
    )
    assert select_relevant_job(jobs, profile) == jobs[1]


def test_select_relevant_job_prefers_kotlin_over_generic_mobile():
    jobs = [
        Job(
            title="Mobile Developer",
            company="Test Startup",
            url="https://example.com/mobile",
        ),
        Job(
            title="Kotlin Developer",
            company="Test Startup",
            url="https://example.com/kotlin",
        ),
    ]

    profile = UserProfile(
        target_roles=["Android Engineer"],
        preferred_skills=["Kotlin", "Jetpack Compose"],
    )
    assert select_relevant_job(jobs, profile) == jobs[1]


def test_select_relevant_job_prefers_jetpack_compose():
    jobs = [
        Job(
            title="Mobile Developer",
            company="Test Startup",
            url="https://example.com/mobile",
        ),
        Job(
            title="Android Developer",
            company="Test Startup",
            url="https://example.com/compose",
            description="Build Android apps with Jetpack Compose.",
        ),
    ]

    profile = UserProfile(
        target_roles=["Android Engineer"],
        preferred_skills=["Kotlin", "Jetpack Compose"],
    )
    assert select_relevant_job(jobs, profile) == jobs[1]


def test_select_relevant_job_prefers_android_development_over_generic_mobile():
    jobs = [
        Job(
            title="Mobile QA Engineer",
            company="Test Startup",
            url="https://example.com/mobile-qa",
            description="Testing Android and mobile applications.",
        ),
        Job(
            title="Android Developer",
            company="Test Startup",
            url="https://example.com/android-dev",
            description="Build Android applications using Kotlin.",
        ),
    ]

    profile = UserProfile(
        target_roles=["Android Engineer"],
        preferred_skills=["Kotlin", "Jetpack Compose"],
    )
    assert select_relevant_job(jobs, profile) == jobs[1]


def test_select_relevant_job_uses_profile_preferences():
    jobs = [
        Job(
            title="Android Developer",
            company="Company A",
            url="https://example.com/a",
        ),
        Job(
            title="Python Backend Developer",
            company="Company B",
            url="https://example.com/b",
        ),
    ]

    profile = UserProfile(
        target_roles=["Backend Developer"],
        preferred_skills=["Python"],
    )

    assert select_relevant_job(jobs, profile) == jobs[1]


def test_select_relevant_jobs_returns_all_relevant_jobs_in_input_order():
    jobs = [
        Job(title="Android Engineer", company="A", url="https://example.com/1"),
        Job(title="Sales Manager", company="A", url="https://example.com/2"),
        Job(title="Kotlin Developer", company="A", url="https://example.com/3"),
        Job(title="Backend Engineer", company="A", url="https://example.com/4"),
        Job(
            title="Mobile Engineer",
            company="A",
            url="https://example.com/5",
            description="Kotlin development",
        ),
    ]
    profile = UserProfile(
        target_roles=["Android Engineer"],
        preferred_skills=["Kotlin", "Compose"],
    )

    assert select_relevant_jobs(jobs, profile) == [jobs[0], jobs[2], jobs[4]]
