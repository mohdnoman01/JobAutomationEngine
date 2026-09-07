from src.research.models import Contact, ContactQualification, UserProfile


def test_contact_preserves_provenance_and_qualification():
    contact = Contact(
        name="Test Recruiter",
        email="recruiter@example.com",
        role="Recruiting",
        company="Test Startup",
        source="https://example.com/careers",
        discovery_type="same_domain_page",
        qualification=ContactQualification.eligible,
        qualification_reason="eligible_role:recruit",
    )

    assert contact.source_url == "https://example.com/careers"
    assert contact.discovery_type == "same_domain_page"
    assert contact.qualification == ContactQualification.eligible
    assert contact.qualification_reason == "eligible_role:recruit"


def test_user_profile_preserves_job_preferences():
    profile = UserProfile(
        target_roles=["Android Developer", "Kotlin Developer"],
        preferred_skills=["Kotlin", "Jetpack Compose"],
        preferred_locations=["Remote", "India"],
        employment_types=["Full-time", "Internship"],
    )

    assert profile.target_roles == ["Android Developer", "Kotlin Developer"]
    assert profile.preferred_skills == ["Kotlin", "Jetpack Compose"]
    assert profile.preferred_locations == ["Remote", "India"]
    assert profile.employment_types == ["Full-time", "Internship"]
