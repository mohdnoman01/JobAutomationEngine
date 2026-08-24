from src.research.contact_discovery import discover_contacts
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