import pytest

from src.research.contact_discovery import (
    deduplicate_contacts,
    discover_contacts,
    discover_relevant_pages,
)
from src.research.models import Company


def test_discover_contacts_extracts_public_emails():
    company = Company(name="Test Startup")

    text = """
    Contact our hiring team at hiring@example.com.
    For general questions email hello@example.com.
    """

    contacts = discover_contacts(text, company)

    assert len(contacts) == 2
    assert contacts[0].email == "hiring@example.com"
    assert contacts[1].email == "hello@example.com"
    assert contacts[0].company == "Test Startup"
    assert contacts[0].source == "public_page"
    assert contacts[0].source_url is None
    assert contacts[0].discovery_type == "public_page"


def test_discover_contacts_removes_duplicates():
    company = Company(name="Test Startup")

    text = """
    hiring@example.com
    hiring@example.com
    """

    contacts = discover_contacts(text, company)

    assert len(contacts) == 1
    assert contacts[0].email == "hiring@example.com"


def test_discover_contacts_returns_empty_when_no_email():
    company = Company(name="Test Startup")

    contacts = discover_contacts(
        "No contact information is available.",
        company,
    )

    assert contacts == []


def test_discover_contacts_extracts_email_from_html_with_source_and_role():
    company = Company(name="Test Startup")

    contacts = discover_contacts(
        '<a href="mailto:talent@example.com">Contact our talent team</a>',
        company,
        source="https://example.com/careers",
    )

    assert len(contacts) == 1
    assert contacts[0].email == "talent@example.com"
    assert contacts[0].company == "Test Startup"
    assert contacts[0].role == "Talent"
    assert contacts[0].source == "https://example.com/careers"
    assert contacts[0].source_url == "https://example.com/careers"
    assert contacts[0].discovery_type == "public_page"


@pytest.mark.parametrize(
    "asset_reference",
    [
        "foo@2x.png",
        "image@3x.jpg",
        "photo@2x.jpeg",
        "logo@2x.webp",
        "icon@3x.svg",
    ],
)
def test_discover_contacts_rejects_image_asset_references(asset_reference):
    company = Company(name="Test Startup")

    contacts = discover_contacts(
        f'<img src="/assets/{asset_reference}">',
        company,
    )

    assert contacts == []


def test_discover_contacts_keeps_legitimate_email_alongside_asset_reference():
    company = Company(name="Test Startup")

    contacts = discover_contacts(
        "Recruiter: recruiter@example.com <img src=\"logo@2x.png\">",
        company,
    )

    assert [contact.email for contact in contacts] == ["recruiter@example.com"]


def test_discover_relevant_pages_keeps_same_host_relevant_links_only():
    html = """
    <a href="/careers/openings">Careers</a>
    <a href="/blog">Blog</a>
    <a href="https://external.example/jobs">External jobs</a>
    <a href="https://www.linkedin.com/company/example">Careers</a>
    """

    pages = discover_relevant_pages(html, "https://example.com")

    assert "https://example.com/careers" in pages
    assert "https://example.com/careers/openings" in pages
    assert "https://example.com/blog" not in pages
    assert "https://external.example/jobs" not in pages
    assert "https://www.linkedin.com/company/example" not in pages


def test_deduplicate_contacts_prefers_role_specific_contact():
    company = Company(name="Test Startup")
    homepage_contact = discover_contacts(
        "general@example.com",
        company,
        source="https://example.com",
    )[0]
    careers_contact = discover_contacts(
        "Recruiting: general@example.com",
        company,
        source="https://example.com/careers",
    )[0]

    contacts = deduplicate_contacts([homepage_contact, careers_contact])

    assert contacts == [careers_contact]
