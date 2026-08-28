from __future__ import annotations

from pathlib import Path

from src.applications.tracker import ApplicationTracker
from src.email.manager import EmailDraftManager
from src.email.templates import create_outreach_email
from src.research.company_loader import load_companies
from src.research.company_researcher import research_company
from src.research.job_discovery import discover_jobs


def run_pipeline(
    companies_path: str | Path,
    *,
    applications_path: str | Path = "data/output/applications.json",
    drafts_path: str | Path = "data/output/email_drafts.json",
) -> dict[str, int]:
    companies = load_companies(str(companies_path))

    application_tracker = ApplicationTracker(applications_path)
    draft_manager = EmailDraftManager(drafts_path)

    companies_processed = 0
    jobs_discovered = 0
    applications_created = 0
    drafts_created = 0

    for company in companies:
        companies_processed += 1

        try:
            research = research_company(company)
            jobs = discover_jobs(company)
        except Exception as exc:
            print(f"[pipeline] {company.name}: failed - {exc}")
            continue

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

            for contact in research.contacts:
                if not contact.email:
                    continue

                email = create_outreach_email(
                    job,
                    company,
                    contact,
                )

                try:
                    draft_manager.add(email)
                except ValueError:
                    continue

                drafts_created += 1

    return {
        "companies_processed": companies_processed,
        "jobs_discovered": jobs_discovered,
        "applications_created": applications_created,
        "drafts_created": drafts_created,
    }