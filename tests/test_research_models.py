from src.research.models import Contact, ContactQualification


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
