from __future__ import annotations

import re

from src.research.contact_discovery import ASSET_FILE_EXTENSIONS

from src.research.models import Contact, ContactQualification, Job, UserProfile


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


def _first_matching_term(value: str, terms: tuple[str, ...]) -> str | None:
    return next((term for term in terms if term in value), None)


def _is_asset_address(email: str) -> bool:
    _, domain = _email_parts(email)
    suffix = domain.rsplit(".", maxsplit=1)[-1]
    return suffix in ASSET_FILE_EXTENSIONS


def classify_contact(contact: Contact) -> tuple[ContactQualification, str]:
    """Classify a discovered contact without removing it from research data."""
    local_part, domain = _email_parts(contact.email)
    role = (contact.role or "").casefold()
    combined = f"{local_part} {domain} {role}"

    if _is_asset_address(contact.email):
        return ContactQualification.rejected, "asset_file_extension"

    if domain in PLACEHOLDER_DOMAINS or local_part in PLACEHOLDER_LOCAL_PARTS:
        return ContactQualification.rejected, "placeholder_address"

    rejected_term = _first_matching_term(combined, REJECTED_TERMS)
    if rejected_term:
        return ContactQualification.rejected, f"rejected_term:{rejected_term}"

    role_term = _first_matching_term(role, ELIGIBLE_TERMS)
    if role_term:
        return ContactQualification.eligible, f"eligible_role:{role_term}"

    address_term = _first_matching_term(local_part, ELIGIBLE_TERMS)
    if address_term:
        return ContactQualification.eligible, f"eligible_address:{address_term}"

    if PERSONAL_ADDRESS_PATTERN.fullmatch(local_part):
        return ContactQualification.uncertain, "personal_looking_address"

    return ContactQualification.uncertain, "no_recruiting_evidence"


def qualify_contact(contact: Contact) -> ContactQualification:
    return classify_contact(contact)[0]


def annotate_contact(contact: Contact) -> Contact:
    qualification, reason = classify_contact(contact)
    return contact.model_copy(
        update={
            "qualification": qualification,
            "qualification_reason": reason,
        }
    )


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


def _job_relevance_score(job: Job, profile: UserProfile) -> int:
    title = job.title.casefold()
    description = (job.description or "").casefold()

    score = 0

    for role in profile.target_roles:
        role = role.casefold().strip()

        if role and role in title:
            score += 10

    for skill in profile.preferred_skills:
        skill = skill.casefold().strip()

        if skill and skill in title:
            score += 8

        if skill and skill in description:
            score += 3

    return score


def is_relevant_job(job: Job, profile: UserProfile) -> bool:
    return _job_relevance_score(job, profile) > 0


def select_relevant_job(
    jobs: list[Job],
    profile: UserProfile,
) -> Job | None:
    if not jobs:
        return None

    return max(
        enumerate(jobs),
        key=lambda item: (_job_relevance_score(item[1], profile), -item[0]),
    )[1]
