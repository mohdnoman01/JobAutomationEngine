from __future__ import annotations

from pathlib import Path

from src.applications.tracker import (
    ApplicationTracker,
    load_contacts,
    save_contacts,
)
from src.email.manager import EmailDraftManager
from src.email.outreach_policy import (
    annotate_contact,
    select_best_contact,
    select_relevant_job,
)
from src.email.templates import create_outreach_email
from src.research.company_loader import load_companies
from src.research.company_researcher import research_company
from src.research.job_discovery import discover_jobs
from src.research.scraper import HttpPerformanceMetrics, PageFetcher


def run_pipeline(
    companies_path: str | Path,
    *,
    applications_path: str | Path = "data/output/applications.json",
    contacts_path: str | Path = "data/output/contacts.json",
    drafts_path: str | Path = "data/output/email_drafts.json",
    http_metrics: HttpPerformanceMetrics | None = None,
) -> dict[str, int]:
    companies = load_companies(str(companies_path))

    application_tracker = ApplicationTracker(applications_path)
    draft_manager = EmailDraftManager(drafts_path)
    page_fetcher = PageFetcher(metrics=http_metrics)
    saved_contacts = load_contacts(contacts_path)
    saved_contact_keys = {
        (contact.company, contact.email)
        for contact in saved_contacts
    }
    saved_contact_indexes = {
        (contact.company, contact.email): index
        for index, contact in enumerate(saved_contacts)
    }
    contacts_changed = False

    companies_processed = 0
    jobs_discovered = 0
    applications_created = 0
    drafts_created = 0

    for company in companies:
        companies_processed += 1

        try:
            research = research_company(company, page_fetcher=page_fetcher)
            jobs = discover_jobs(company, page_fetcher=page_fetcher)
        except Exception as exc:
            print(f"[pipeline] {company.name}: failed - {exc}")
            continue

        for contact in research.contacts:
            audited_contact = annotate_contact(contact)
            contact_key = (audited_contact.company, audited_contact.email)

            if contact_key in saved_contact_keys:
                index = saved_contact_indexes[contact_key]

                if saved_contacts[index] != audited_contact:
                    saved_contacts[index] = audited_contact
                    contacts_changed = True

                continue

            saved_contact_keys.add(contact_key)
            saved_contact_indexes[contact_key] = len(saved_contacts)
            saved_contacts.append(audited_contact)
            contacts_changed = True

        jobs_discovered += len(jobs)

        for job in jobs:
            application = application_tracker.get(job.url)

            if application is None:
                application = application_tracker.create(
                    company=company.name,
                    job_title=job.title,
                    job_url=job.url,
                )
                applications_created += 1

        contact = select_best_contact(research.contacts)
        job = select_relevant_job(jobs)

        if contact is not None and job is not None:
            email = create_outreach_email(job, company, contact)

            try:
                draft_manager.add(email)
            except ValueError:
                pass
            else:
                drafts_created += 1

    if contacts_changed:
        save_contacts(saved_contacts, contacts_path)

    return {
        "companies_processed": companies_processed,
        "jobs_discovered": jobs_discovered,
        "applications_created": applications_created,
        "drafts_created": drafts_created,
    }
