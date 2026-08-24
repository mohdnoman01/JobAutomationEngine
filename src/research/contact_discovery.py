from __future__ import annotations

import re

from src.research.models import Company, Contact

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def discover_contacts(
    text: str,
    company: Company,
    source: str = "public_page",
) -> list[Contact]:
    contacts: list[Contact] = []
    seen_emails: set[str] = set()

    for match in EMAIL_PATTERN.findall(text):
        email = match.strip().lower()

        if email in seen_emails:
            continue

        seen_emails.add(email)

        contacts.append(
            Contact(
                email=email,
                company=company.name,
                source=source,
            )
        )

    return contacts
