from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from src.research.models import Company, Contact

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
ASSET_FILE_EXTENSIONS = frozenset(
    {
        "avif",
        "bmp",
        "css",
        "eot",
        "gif",
        "ico",
        "jpeg",
        "jpg",
        "js",
        "map",
        "mov",
        "mp3",
        "mp4",
        "pdf",
        "png",
        "svg",
        "ttf",
        "wav",
        "webm",
        "webp",
        "woff",
        "woff2",
    }
)
RELEVANT_LINK_PATTERN = re.compile(
    r"\b(?:about|careers?|company|contacts?|hiring|human[\s-]?resources|"
    r"jobs?|recruit(?:er|ing|ment)?|talent|team|hr)\b",
    re.IGNORECASE,
)
ROLE_PATTERNS = (
    ("Recruiting", re.compile(r"\b(?:recruiter|recruiting|recruitment)\b", re.I)),
    ("Talent", re.compile(r"\btalent\b", re.I)),
    ("Human Resources", re.compile(r"\b(?:human[\s-]?resources|hr)\b", re.I)),
    ("Hiring", re.compile(r"\bhiring\b", re.I)),
)
FALLBACK_CONTACT_PATHS = (
    "/careers",
    "/contact",
)
MAX_CONTACT_PAGES = 12


def _infer_role(text: str, match_start: int, match_end: int) -> str | None:
    context = text[max(0, match_start - 120): match_end + 120]

    for role, pattern in ROLE_PATTERNS:
        if pattern.search(context):
            return role

    return None


def _contact_priority(contact: Contact) -> int:
    return int(contact.role is not None)


def _is_contact_email(email: str) -> bool:
    """Reject file-path fragments that resemble email addresses in raw HTML."""
    _, domain = email.rsplit("@", maxsplit=1)
    suffix = domain.rsplit(".", maxsplit=1)[-1].lower()

    return suffix not in ASSET_FILE_EXTENSIONS


def discover_contacts(
    text: str,
    company: Company,
    source: str = "public_page",
    discovery_type: str = "public_page",
) -> list[Contact]:
    contacts: list[Contact] = []
    seen_emails: set[str] = set()

    for match in EMAIL_PATTERN.finditer(text):
        email = match.group().strip().lower()

        if not _is_contact_email(email) or email in seen_emails:
            continue

        seen_emails.add(email)

        contacts.append(
            Contact(
                email=email,
                company=company.name,
                role=_infer_role(text, match.start(), match.end()),
                source=source,
                source_url=source if source.startswith(("http://", "https://")) else None,
                discovery_type=discovery_type,
            )
        )

    prioritized_contacts = sorted(
        enumerate(contacts),
        key=lambda item: (-_contact_priority(item[1]), item[0]),
    )

    return [contact for _, contact in prioritized_contacts]


def discover_relevant_pages(html: str, website_url: str) -> list[str]:
    """Return a bounded list of same-host pages suitable for contact lookup."""
    base = urlsplit(website_url)

    if base.scheme not in {"http", "https"} or not base.netloc:
        return []

    root_url = urlunsplit((base.scheme, base.netloc, "/", "", ""))
    pages: list[str] = []
    seen_urls = {urldefrag(website_url)[0]}

    def add_if_same_host(url: str) -> None:
        normalized_url = urldefrag(url)[0]
        parsed = urlsplit(normalized_url)

        if (
            parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() != base.netloc.lower()
            or normalized_url in seen_urls
            or len(pages) >= MAX_CONTACT_PAGES
        ):
            return

        seen_urls.add(normalized_url)
        pages.append(normalized_url)

    soup = BeautifulSoup(html, "html.parser")

    for link in soup.find_all("a", href=True):
        url = urljoin(website_url, link["href"].strip())
        link_context = f"{link.get_text(' ', strip=True)} {urlsplit(url).path}"

        if RELEVANT_LINK_PATTERN.search(link_context):
            add_if_same_host(url)

    if not pages:
        for path in FALLBACK_CONTACT_PATHS:
            add_if_same_host(urljoin(root_url, path))

    return pages


def deduplicate_contacts(contacts: list[Contact]) -> list[Contact]:
    """Keep one contact per email, preferring entries with a relevant role."""
    unique_contacts: dict[str, tuple[int, Contact]] = {}

    for index, contact in enumerate(contacts):
        existing = unique_contacts.get(contact.email)

        if existing is None or _contact_priority(contact) > _contact_priority(existing[1]):
            unique_contacts[contact.email] = (index, contact)

    return [
        contact
        for _, contact in sorted(
            unique_contacts.values(),
            key=lambda item: (-_contact_priority(item[1]), item[0]),
        )
    ]
