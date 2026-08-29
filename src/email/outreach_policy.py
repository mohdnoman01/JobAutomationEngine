from __future__ import annotations

import re
from enum import StrEnum

from src.research.contact_discovery import ASSET_FILE_EXTENSIONS
from src.research.models import Contact, Job


class ContactQualification(StrEnum):
    eligible = "eligible"
    uncertain = "uncertain"
    rejected = "rejected"


ELIGIBLE_TERMS = ("recruit", "talent", "human resources", "hr", "hiring")
REJECTED_TERMS = (
    "abuse",
    "billing",
    "cancel",
    "customer service",
    "help",
    "ingest",
    "marketing",
    "media",
    "press",
    "privacy",
    "renewal",
    "sales",
    "sentry",
    "sponsor",
    "support",
    "telemetry",
    "upgrade",
)
PLACEHOLDER_DOMAINS = {"company.com"}
PLACEHOLDER_LOCAL_PARTS = {"email", "name", "test", "you"}
ROLE_SCORES = {
    "recruit": 4,
    "talent": 3,
    "human resources": 2,
    "hr": 2,
    "hiring": 1,
}
PERSONAL_ADDRESS_PATTERN = re.compile(r"^[a-z]+(?:[._-][a-z]+)*$")


def _email_parts(email: str) -> tuple[str, str]:
    local_part, domain = email.casefold().rsplit("@", maxsplit=1)
    return local_part, domain


def _contains_term(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def _is_asset_address(email: str) -> bool:
    _, domain = _email_parts(email)
    suffix = domain.rsplit(".", maxsplit=1)[-1]
    return suffix in ASSET_FILE_EXTENSIONS


def qualify_contact(contact: Contact) -> ContactQualification:
    """Classify a discovered contact without removing it from research data."""
    local_part, domain = _email_parts(contact.email)
    role = (contact.role or "").casefold()
    combined = f"{local_part} {domain} {role}"

    if (
        _is_asset_address(contact.email)
        or domain in PLACEHOLDER_DOMAINS
        or local_part in PLACEHOLDER_LOCAL_PARTS
        or _contains_term(combined, REJECTED_TERMS)
    ):
        return ContactQualification.rejected

    if _contains_term(f"{local_part} {role}", ELIGIBLE_TERMS):
        return ContactQualification.eligible

    if PERSONAL_ADDRESS_PATTERN.fullmatch(local_part):
        return ContactQualification.uncertain

    return ContactQualification.uncertain


def _eligible_contact_score(contact: Contact) -> int:
    role = (contact.role or "").casefold()
    local_part, _ = _email_parts(contact.email)
    score = 0

    for term, value in ROLE_SCORES.items():
        if term in role:
            score = max(score, value * 10)
        if term in local_part:
            score = max(score, value)

    return score


def rank_eligible_contacts(contacts: list[Contact]) -> list[Contact]:
    ranked_contacts = [
        (index, contact)
        for index, contact in enumerate(contacts)
        if qualify_contact(contact) == ContactQualification.eligible
    ]

    ranked_contacts.sort(
        key=lambda item: (-_eligible_contact_score(item[1]), item[0]),
    )
    return [contact for _, contact in ranked_contacts]


def select_best_contact(contacts: list[Contact]) -> Contact | None:
    ranked_contacts = rank_eligible_contacts(contacts)
    return ranked_contacts[0] if ranked_contacts else None


def _job_relevance_score(job: Job) -> int:
    content = f"{job.title} {job.description or ''}".casefold()

    if "android" in content:
        return 4
    if "kotlin" in content:
        return 3
    if "mobile" in content:
        return 2
    if "software engineer" in content or "software developer" in content:
        return 1
    return 0


def select_relevant_job(jobs: list[Job]) -> Job | None:
    if not jobs:
        return None

    return max(
        enumerate(jobs),
        key=lambda item: (_job_relevance_score(item[1]), -item[0]),
    )[1]
